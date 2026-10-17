# 002: projects and CRUD

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-17 |
| Builds on | [001](001-wandb-compatible-tracker.md) |
| Amends | D3 (`file_stream` no longer creates runs), D7 (new live events), D8 (the projects half), and the paths of the POST query endpoints |
| Keeps | D2 (in-memory working set, one server per database), and D8's auth |
| Result | 133 tests in `tests/tracker/` pass; see [section 6](#6-how-it-was-actually-built) for where the build departed from the plan |

## 1. Goal

001 treated a project as a label on its runs: the text pair `(entity, project)`. A project
existed only while it had at least one run, and nothing about a run could change except
through the wandb SDK. This stage makes two changes:

1. **Projects become records.** They're stored in a `projects` table, can be empty, and
   can be created, renamed, described and deleted through the API. `wandb.init` still
   creates them automatically, as wandb does.
2. **Every existing resource gets a deliberate CRUD story.** [Section 3](#3-crud-matrix)
   lists, for each resource, what can be created, read, updated and deleted, and by whom
   (the SDK or the API). New in this stage:
   - editing run metadata;
   - moving runs between projects;
   - **hard** deletion of runs and projects.

**Out of scope:**
- organizations, teams, and renaming entities (a later stage);
- auth changes: D8 stays as it is;
- undo or restore. Deletes are hard, so there's nothing to restore;
- editing config, summary or history;
- deleting individual run files.

**Constraint:** the latency budgets from 001 still hold. Mutations may be slower (they're
rare admin actions), but the read paths must not get slower because of them.

---

## 2. Design decisions

The numbering continues from 001.

### D11. No Redis; D2 stands

This stage was first scoped to move the in-memory state into Redis, and that was rejected.
The in-memory column table and series cache are not just a cache in front of PostgreSQL; they
are the query engine. Filters are vectorized numpy scans over every run, and curves are
slices of arrays already in memory.

Moving that state to Redis would cost either a network round trip per column or curve, or
a Redis-side query engine (Redis Query Engine). That engine's filter semantics don't
match the Mongo-style ones in use:
- missing-value rules;
- list membership;
- `$regex`;
- sorting on any of 1.5k dynamic columns.

None of Redis's benefits are needed today: multiple replicas, fast restarts, sharing state
with other services. Revisit only if running more than one replica becomes a requirement.

### D12. A `projects` table, and runs reference it

Migration `0003_projects.sql`:

```sql
CREATE TABLE projects (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity text NOT NULL,
    name text NOT NULL,
    description text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (entity, name)
);

INSERT INTO projects (entity, name, created_at, updated_at)
SELECT entity, project, min(created_at), max(updated_at) FROM runs GROUP BY entity, project;

ALTER TABLE runs ADD COLUMN project_pk bigint REFERENCES projects ON DELETE CASCADE;
UPDATE runs SET project_pk = p.pk FROM projects p
WHERE p.entity = runs.entity AND p.name = runs.project;
ALTER TABLE runs ALTER COLUMN project_pk SET NOT NULL;
ALTER TABLE runs DROP CONSTRAINT runs_entity_project_name_key;
ALTER TABLE runs ADD CONSTRAINT runs_project_pk_name_key UNIQUE (project_pk, name);
ALTER TABLE runs DROP COLUMN entity, DROP COLUMN project;
```

- **Renames and moves become one-row updates.** Renaming a project changes one `projects`
  row, and moving a run changes one `runs.project_pk`.
- **Files never move.** They live under `files_dir/<run pk>/`, and the series cache is keyed
  by run pk, so neither changes when a run is renamed or moved.
- **Deletes cascade.** Deleting a project removes its runs, and each run's lines,
  compacted series and file rows go with it.
- **Entity stays free-form text** on the project. The UI's `/[entity]/[project]` routes and
  wandb's paths still need it. A real entities table comes with organizations.
- **Auto-creation:** `UpsertBucket` creates a missing project, and so does the fallback to
  `TRACKER_DEFAULT_PROJECT` (`uncategorized`).
- **Empty projects are allowed:** projects created through the API, and projects whose last
  run was deleted or moved out.

### D13. In-memory model

Changes, mostly in `src/core/store.py`:

- **`Project` gains fields:** `pk`, `description`, `created_at` and `updated_at`.
  - It also gets an `asyncio.Lock`. Creating a run, moving runs in or out, deleting runs,
    and renaming or deleting the project all take it.
  - It gets a per-stream `Counter` of how many runs have each metric key. This replaces
    `metric_keys`, which could only grow. A key disappears from the dashboard once no run
    in the project logs it.
- **`Run` changes:**
  - `run.project` becomes a reference to its `Project` instead of a string. `entity` and
    the project name are read through it, so a rename is O(1) per run.
  - Each run keeps its own key set per stream, so the project counts can be decremented.
    It comes from `compacted_keys` when the run is compacted, and from the cached series
    otherwise.
  - A `deleted` flag is set once the run's delete commits. Anything still holding the
    `Run` object checks it and stops instead of writing: an in-flight `file_stream`, a
    background compaction, a history rebuild.
- **Store indexes:**
  - `Store.projects` stays keyed by `(entity, name)` and is re-keyed on rename.
  - It gains `projects_by_pk`.
  - At startup, every project is loaded first, empty ones included. Runs are then attached
    via `project_pk`.
- **Locks:** per-run locks (`Store._lock`) are keyed by `run.pk` instead of name tuples,
  which go stale on rename or move. Locks are always taken in the same order, so there are
  no deadlocks:
  - project locks first, in pk order (a move takes two);
  - then run locks, in pk order.

  `file_stream` takes only its run's lock. It resolves the run by path, acquires the lock,
  then re-checks `run.deleted`.

### D14. Removing rows from the run table

Today a row index is both the run's position in `project.runs` and its row in
`ColumnTable`. Rows are only ever appended, in creation order, and the default sort
(`rows[::-1]`) relies on that order.

- **Invariant:** a project's rows stay ordered by `(created_at, pk)`.
- **New primitive:** `ColumnTable.take(index)` builds every column again from the rows in
  `index`, in that order: number, object and presence arrays. Columns left with no values
  are dropped. After every `take`, `project.runs` is rebuilt in the same order and each
  `run.row` is renumbered.
- **Delete** keeps the surviving rows: `take(surviving)`.
- **Move:**
  1. Read the run's cells from the source table (new `ColumnTable.row_values(row)`).
  2. `take` the source without that row.
  3. Append the row to the destination.
  4. `take` the destination sorted by `created_at`, so the moved run lands at its creation
     time rather than looking like the newest run.
- **Cost:** O(runs × columns), on the order of tens of ms for 10k runs × 1.5k columns. That's
  fine for rare admin actions. Batch operations do one `take` per affected project, not one
  per run.
- **Concurrency rule:** a row index is valid only until the next `await`. The existing
  readers already turn rows into `Run` objects (or copy the cells) before they await:
  - `RunSelection.select` and then `query_runs`;
  - `query_history`;
  - `Snapshot.build`, which copies table cells before it awaits `store.histories`;
  - `stream`.

  Keep it that way, and add a test that interleaves a delete with these reads.
- **The series cache must not keep deleted runs.** `histories()` and `_rebuild_history`
  must not cache a series for a deleted run. A rebuild that finishes after the delete
  returns its result without caching it. `_schedule_compaction` skips deleted runs and
  treats a foreign-key violation (the run vanished mid-compaction) as expected rather than
  as an error.

### D15. Hard deletes, and live wandb clients

- **Order of operations:**
  1. Take the project lock, then the run locks.
  2. Run `DELETE FROM runs WHERE pk = ANY($1)` (or `DELETE FROM projects WHERE pk = $1`)
     in one transaction. It cascades to lines, compacted series and `run_files`.
  3. Once it commits, clean up memory synchronously, with no await in between:
     - set `run.deleted`;
     - remove the run from `project.runs`, `by_name`, `active`, `Store.active` and
       `runs_by_pk`;
     - `take` the table;
     - decrement the metric-key counts;
     - drop cached series and in-flight rebuilds;
     - drop the run's lock.
  4. Publish the live events.
  5. Remove `files_dir/<pk>` in a thread (`asyncio.to_thread`).

  If the process dies between steps 2 and 5, the file directories are orphaned. At startup,
  the store removes directories whose pk isn't in `runs`.
- **Large deletes are synchronous.** A project with millions of line rows can take seconds
  to cascade-delete. The request waits, and reads keep seeing the data until the
  transaction commits. That's acceptable for now; see the known gaps.
- **Active runs** (state `running` or `preempting`):
  - deleting one returns 409 unless the request passes `force=true`;
  - moving one always returns 409;
  - renaming a project that has active runs always returns 409.

  The reason: a live client addresses its run by `entity/project/run` in every
  `file_stream` URL, so moving or renaming underneath it would split the run.
  Crashed-but-undetected runs become inactive after `crash_timeout`. `force` is for when
  you don't want to wait.
- **`file_stream` no longer creates runs (amends D3).** Today it creates a missing run
  (`store.py`, in `file_stream`), so a client whose run was force-deleted would bring back
  a partial run, possibly in a project that was just deleted. Only `UpsertBucket` creates
  runs from now on. `file_stream` for an unknown run returns 404.
- **Gate:** before relying on the 404, verify with the real SDK that it never sends
  `file_stream` before `UpsertBucket`, in normal runs, resumes and `wandb sync`. Also verify
  that a 404 doesn't crash training or hang `run.finish()`. If either fails, the fallback
  is to remember force-deleted run paths in memory (bounded, not durable) and answer their
  `file_stream` with 200 while discarding the data.
- **Resuming a moved or deleted run.** `RunResumeStatus` at the old path returns `null`.
  `resume="must"` then fails, and `resume="allow"` starts a new run at the old path,
  auto-creating the project if needed. That matches wandb when a run has been deleted.
- **No tombstones.** A hard delete leaves nothing behind in the database.

### D16. API shape and validation

- **POST on a collection means create.** In 001, `POST .../runs` and `POST .../history`
  were queries with a JSON body. They move to `POST .../runs/query` and
  `POST .../history/query`, which take the same bodies. The GET variants are unchanged.
  flock-ui only calls `/projects`, `/workspace` and `/stream`, so only the tests and the
  README change.
- **Runs are still created only by wandb.** There's no `POST .../runs` for creating runs;
  that path is simply freed.
- **Bulk selections** reuse `RunSelection` (`src/api/runs.py`), which provides `filter`
  and `running`, and add `runs` (explicit ids) and `all`. The body must name `runs`, give
  a `filter`, or set `all: true`, so an empty body never selects everything. `dry_run: true`
  returns the ids that would be affected without changing anything, so the UI can confirm
  before deleting.
- **Bulk operations are all-or-nothing.** If any selected run is active (without `force`)
  or would collide with a run name in the destination, the request returns 409, listing the
  offending ids, and changes nothing.
- **Project name rules:** names created or renamed through the API must be 1–128
  characters, with no surrounding whitespace, no control characters and none of
  `/ \ # ? % :` (wandb's rule). Names that arrive through the SDK are accepted as they
  come, since the SDK has already validated them.
- **Fixed identifiers:** entities can't be renamed (that comes with organizations), and
  run ids (wandb's `name`) never change. Display names can.
- **Clearing fields:** `PATCH` distinguishes a field the client left out from an explicit
  `null`, using pydantic's `model_fields_set`. `null` clears notes, group, job type or
  description. `_update_run` currently ignores `None`, so it needs a way to clear a field.
- **Concurrent edits: last write wins.** If the SDK sends tags or notes after a UI edit
  (for example, `run.tags = ...` mid-run), the SDK's values replace the edit. This is
  documented, not prevented.

### D17. Live events (amends D7)

| Event | When | Payload |
| --- | --- | --- |
| `run` (extended) | metadata edit, state change | as before, plus `attributes`: the changed fields among `display_name`, `tags`, `notes`, `group`, `job_type`, `description` |
| `deleted` (new) | runs deleted, or moved out | `{"runs": [...]}`, plus `"moved_to": "<project>"` for moves |
| `created` (existing) | a run is created, or moved in | unchanged |
| `project` (new) | project renamed or deleted | `{"renamed_to": "<name>"}` or `{"deleted": true}` |

After a `project` event the server closes that project's streams, because the hub is keyed
by `(entity, name)`. The client follows the rename, or leaves the page. flock-ui has to
handle `deleted` and `project`; that's a follow-up for the UI.

### Unchanged

- **D8's auth:** any valid key, or any key at all when `TRACKER_API_KEYS` is empty, can
  rename and delete. With destructive endpoints added, that matters more. Keep the tracker
  behind a network boundary until auth gets its own stage.
- **D3's idempotent, single-statement write path** is unchanged apart from the 404 for
  unknown runs.

---

## 3. CRUD matrix

All paths start with `/api/v1/projects`. Run paths are relative to `/{entity}/{project}`.

| Resource | Create | Read | Update | Delete |
| --- | --- | --- | --- | --- |
| project | `POST /api/v1/projects` `{entity, name, description?}`; auto-created by `wandb.init` | `GET /api/v1/projects[?entity=]`; **new** `GET /{entity}/{project}` | `PATCH /{entity}/{project}` `{name?, description?}` | `DELETE /{entity}/{project}[?force=true]`, which cascades to its runs |
| run | wandb SDK only | `GET .../runs`, `POST .../runs/query`, `GET .../runs/{run}` | `PATCH .../runs/{run}` `{display_name, notes, tags, group, job_type, description}`; bulk `POST .../runs/update` `{selection, set?, add_tags?, remove_tags?}`; `POST .../runs/move` `{selection, to}` | `DELETE .../runs/{run}[?force=true]`; bulk `POST .../runs/delete` `{selection, force?, dry_run?}` |
| config, summary | wandb SDK | run detail, table columns | wandb SDK only | with the run |
| history, system, logs | wandb SDK (append-only) | existing GETs | none | with the run |
| run files | wandb signed upload | existing GETs | wandb re-upload | with the run |
| columns, workspace, stream | derived | existing GETs | none | none |

Responses:
- `POST /projects`: 201 with the project, or 409 if it exists.
- `PATCH`: 200 with the updated resource.
- `DELETE`: 204.
- Bulk endpoints: `{"ids": [...], "count": n}`, plus `"dry_run": true` when nothing
  changed.
- `GET /projects` and `GET /{entity}/{project}` return `entity`, `name`, `description`,
  `created_at`, `updated_at`, `run_count`, `active_run_count` and `column_count`.

---

## 4. Plan

As in 001: each step says what to build, when it's done, and the traps to avoid. Run the
whole tracker suite after every step. The latency tests are the guard on the read path.

### Step 1. Migration 0003

- **Build:** `src/db/migrations/0003_projects.sql`, as written in D12.
- **Done when:**
  - `test_migrations.py` passes (numbering is still contiguous);
  - a new `test_0003_creates_projects_from_runs` passes. Following
    `test_0002_upgrades_existing_data`, it seeds runs at schema 0002, migrates them, and
    checks that every run has a project, that `created_at` is the earliest run's, and that
    the unique key is now `(project_pk, name)`.
- **Pitfall:** the backfill and the `NOT NULL` must be in the same migration. The
  migration runner wraps each file in a transaction, so a failure leaves nothing
  half-applied.

### Step 2. Store and model refactor (D13)

- **Build:**
  - Load `projects` before `runs`. The runs query joins on `project_pk`.
  - `Run.project` becomes a reference to its `Project`.
  - Key locks by pk.
  - Add the metric-key counts and per-run key sets. Update them in `file_stream` (where
    `note_metrics` is called today), in compaction and at startup.
  - `_create_run` inserts the project row first if needed (`INSERT ... ON CONFLICT (entity,
    name) DO UPDATE ... RETURNING pk`), then the run with `project_pk`.
  - `file_stream` returns 404 for unknown runs (D15).
- **Done when:** the existing suite passes unchanged, except for tests that relied on
  `file_stream` creating runs. Those now call `create_run` first.
- **Pitfall:** `run.project` is used as a string in many places (`Run.path`,
  `graphql_id`, the hub calls, `Store.projects[(run.entity, run.project)]`). Change every
  use, and let ty catch the misses.

### Step 3. Removing rows from the table (D14)

- **Build:** `ColumnTable.take(index)` and `ColumnTable.row_values(row)` in
  `src/core/table.py`, plus a store helper that runs `take`, rebuilds `project.runs` and
  renumbers rows.
- **Done when:**
  - a unit test passes: after `take`, filters, sorts and `select_columns` give the same
    answers as a table built from scratch with only the kept rows, and empty columns are
    gone;
  - a latency test passes: `take` dropping 1k rows from a 10k × 1.5k table finishes in
    under 200 ms;
  - the filter p50 afterwards is unchanged.

### Step 4. Project endpoints

- **Build:**
  - the project endpoints from the matrix;
  - name validation;
  - `ProjectDep` now finds empty projects too;
  - rename: update the row, re-key `Store.projects`, send the `project` event, close that
    project's streams.
- **Done when:** a new `tests/tracker/test_projects.py` passes. It covers:
  - create, get, list (`?entity=`) and patch;
  - 409 on duplicate names, and 422 on invalid names;
  - empty projects survive a restart;
  - `wandb.init` into an unknown project creates it;
  - rename with active runs returns 409, and without them succeeds, after which old paths
    return 404;
  - delete cascades to runs, lines, series and files on disk.

### Step 5. Editing run metadata

- **Build:**
  - `PATCH .../runs/{run}` through `_update_run`, with explicit `null` clearing fields.
  - Bulk `POST .../runs/update`: one SQL statement for the whole selection (`unnest` over
    pk and value arrays). Tag add and remove are computed per run.
  - `run` events carry the changed attributes.
- **Done when:** a new `tests/tracker/test_run_mutations.py` covers:
  - each field: set, clear, and visible as a filterable column at once;
  - bulk tag add and remove by filter;
  - the `run` event payload;
  - persistence across a restart.

### Step 6. Deleting runs

- **Build:** single and bulk delete, following D15. Also the startup sweep for orphaned
  file directories, the `deleted` event, and the deleted-run guards in `histories`,
  `_rebuild_history` and `_schedule_compaction`.
- **Done when** `test_run_mutations.py` also covers:
  - delete and bulk delete by ids and by filter;
  - `dry_run`;
  - an empty selection is rejected;
  - an active run returns 409, and `force` deletes it;
  - the deleted run's rows and files are gone in PostgreSQL and on disk, and memory is
    consistent: `total` counts, metric keys and the run table;
  - a delete interleaved with an in-flight snapshot or history read leaves no cached series
    behind;
  - a restart after a simulated crash between commit and file removal sweeps the orphaned
    directory.

### Step 7. Moving runs

- **Build:** `POST .../runs/move`, following D14 and D16. Validate the destination in the
  same entity, auto-creating it only if `create: true`. A name collision returns 409.
  Sources get `deleted` with `moved_to`, and the destination gets `created`.
- **Done when** `test_run_mutations.py` also covers:
  - moved runs keep their history, system metrics, files and summary;
  - they sort at their creation time in the destination;
  - metric keys move with them;
  - moving an active run returns 409;
  - a collision returns 409 and changes nothing.

### Step 8. Moving the query endpoints, and docs

- **Build:**
  - rename the POST query routes to `/runs/query` and `/history/query`;
  - update the tests that call them;
  - update the README: the read API table (plus a CRUD section), the stream events, and a
    note in "What is supported" that projects, edits, moves and deletes are now covered.
- **Done when:** a grep finds no remaining callers of `POST .../runs` or
  `POST .../history` outside the README's migration note.

### Step 9. Real-SDK checks and latency

- **Build:** new cases in `test_wandb_integration.py`:
  - **Log, move, resume:** a run that finished, was moved and is then resumed with
    `resume="allow"` at the old path becomes a new run in the old project. The moved run is
    unchanged.
  - **Force-delete while logging:** force-delete a run while the SDK is logging. The
    process must exit normally, `run.finish()` must return, and the run must not come back.
    This is the D15 gate. If it fails, implement the fallback before going on.
  - **Offline sync after delete:** `wandb sync` of an offline run into a deleted project
    recreates the project and the run.

  Add latency cases to `test_latency.py`: the bulk delete of 1k runs, plus the existing
  filter and curve budgets re-measured after deletes and moves.
- **Done when:** the whole suite passes, with every latency budget still met, and ruff and
  ty are clean.

### Step 10. Mark the stage as applied

- **Build:** set this file's status to `applied`, and add a short "How it was actually
  built" note if the implementation departed from the plan. Don't otherwise edit the
  decisions. Changes of direction go in 003.

---

## 5. Known gaps after this stage (candidates for 003+)

- **Auth (D8, unchanged):** destructive endpoints can be called by any key, or by anyone
  when keys aren't configured.
- **Large deletes are synchronous:** deleting a big project holds the request open for the
  whole cascade. A background deletion job, run after the project is hidden, would fix
  that.
- **No undo:** hard deletes are final, and backups are the only recovery.
- **No organizations or entity records:** entities stay free-form strings, and projects
  can't move between entities.
- **Not editable:** config and summary can't be edited, and run files can't be deleted
  individually.
- **The UI still lacks** handling for the `deleted` and `project` events, and screens for
  the new operations.

---

## 6. How it was actually built

The plan was followed in order. These are the places where the code differs from the
decisions above, and why.

- **The D15 gate failed, so the fallback is in.** Captured traffic showed that after a
  forced delete, the SDK's client doesn't just keep calling `file_stream`. When it finishes, it
  sends `UpsertBucket` again (a config update), which would have recreated the run. It then
  asks for upload URLs, uploads the run's files, and streams its whole history from offset 0.
  - **What was built:** the store remembers runs force-deleted while active
    (`Store._deleted_live`). `Store.discarding` makes `UpsertBucket`, `file_stream`,
    `CreateRunFiles` and uploads for those paths succeed while dropping the data.
  - **When the mark lifts:** once the client has been quiet for `crash_timeout`, the run id is
    free again.
  - **Cost:** re-using the same run id at the same path within that window is silently
    dropped. Only forced deletes of active runs set the mark.
  - **Tested by:** `test_force_deleted_run_stays_deleted_while_its_client_finishes`, which
    confirms that the client exits normally and the run stays gone.
- **Moves copy rows column by column, not cell by cell (D14).** The planned
  `ColumnTable.row_values` + `set_many` path took ~340 ms to move 500 runs of a 10k × 1.5k
  table, over the 200 ms target. It was replaced by `ColumnTable.append_from(other, rows)`,
  which copies every column's arrays for those rows at once, taking ~50 ms. `row_values`
  doesn't exist.
- **The live hub is keyed by project pk,** not `(entity, name)` (D17). A rename therefore
  doesn't need to re-key subscriptions, and streams are still ended so clients follow the new
  name.
- **Errors:** the store raises `NotFound` (404) and `Conflict` (409, with `ids`). `app.py`
  maps `Conflict` to a response. `APIError` gained extra body fields, used to list missing run
  ids.
- **Locking helper:** `Store._locked(projects, runs, all_runs=False)` takes the project locks,
  then the run locks, in pk order. `all_runs=True` lists a project's runs only after its lock
  is held. That's for project deletes, which must not miss a run created in the meantime.
  `Store.ensure_project` is public, because `POST .../runs/move` with `create: true` uses it.
- **Test harness:** `TrackerServer.stop()` now ends live streams before shutting down, as the
  app does on SIGTERM. Otherwise a test that restarts a server with a dashboard stream still
  open waited for the stream, and the restarted server couldn't take the database lock.

Measured at this checkpoint, on the same machine as 001:
- deleting 953 runs from the 10k × 1.5k table takes ~90–120 ms;
- moving 500 runs takes ~50 ms;
- filter and curve latencies are unchanged.
