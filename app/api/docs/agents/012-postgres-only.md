# 012: PostgreSQL as the only state, so Flock can run as many replicas as it needs

| | |
| --- | --- |
| Status | applied |
| Date | 2026-10-28 |
| Builds on | [001](001-wandb-compatible-flock.md), [002](002-projects-and-crud.md), [003](003-organizations-and-auth.md), [004](004-home-and-budgets.md), [006](006-wandb-import.md), [008](008-cli-login.md), [009](009-s3-proxy.md) |
| Replaces | D2 (one process's memory, one server per database), D4 (numpy run table), D5 (hot series in memory, LRU of cold ones), D13 (in-memory model) |
| Amends | D3 (the write path also writes series and run values), D7/D17 (live events cross replicas), D9 (deployment), D15 (deleted-while-live runs are a table), D37 (imports survive a replica dying) |
| Keeps | D1, D6, D8–D12, D14, D16, D18–D75 except where amended above |
| Result | 598 tests in `tests/app`, `tests/cli` and `tests/s3` pass on moto (7 new in `tests/app/test_replicas.py`, 2 in `tests/s3/test_files_backend.py`, the PostgreSQL oracle and regex tests in `test_table.py`, chunk and long-value tests in `test_query_api.py`); `app/web` unchanged. 11 of 13 latency tests pass; several-metric curves (12.3 ms p50) and moves (3.1 s) miss; see [section 6](#6-how-it-was-actually-built) |

## 1. Goal

Flock answers every read from its own memory today (D2). PostgreSQL is the source of truth, but
the process loads all runs, the run tables, the active runs' curves and every account at startup,
and holds an advisory lock so no second server can start. That was the right call for a
prototype with a hard latency budget. It now costs more than it saves:

- **No high availability.** One replica, so a deploy, a node drain or an OOM kill takes tracking
  down. wandb clients retry, but dashboards and `flock login` don't.
- **Startup and memory grow with the data.** Every run is parsed at startup, and memory grows with
  runs × columns. On Kubernetes this means slow rollouts and a pod sized for the largest org.
- **Two copies of every rule.** Each write updates PostgreSQL and then the in-memory copy, by
  hand, under locks that only exist in this process. Every feature since 002 has had to get that
  twice right (`_forget_run`, `_reindex`, `_deleted_live`, the history LRU, budget accrual).

This stage makes PostgreSQL the only state. Any replica can serve any request, a replica holds
nothing that a restart loses, and the server lock goes. Concretely:

1. **Reads come from PostgreSQL** (D76). Run tables, filters, curves, the feed, budgets and auth
   are queries, with small caches only where a notification keeps them correct.
2. **Filters run in SQL** over a per-run value index (D77), with the same Mongo-style language and
   the same semantics as `table.py`.
3. **Curves are stored as appendable chunks** (D78), written with the line that carries them, so
   active and finished runs are read the same way.
4. **Live events cross replicas** through `LISTEN`/`NOTIFY` (D79).
5. **Locks are PostgreSQL locks** (D80), background work is claimed through the database (D81),
   and run files go to shared storage (D82).
6. **Kubernetes-ready deployment** (D83): stateless replicas, a migration Job, readiness probes.

**Out of scope:**
- a Helm chart or a Kubernetes operator (D83 lists what a manifest needs; writing one is later);
- PostgreSQL HA itself (Patroni, CloudNativePG, a managed service). Flock takes a DSN and works
  with any of them;
- read replicas. Every query goes to the primary for now (section 4);
- changing the wandb protocol, the REST API or the web app. The API stays byte for byte the same;
- Redis or any other new service. D11 stands: PostgreSQL is enough.

---

## 2. Design decisions

The numbering continues from 011.

### D76. What moves where

Everything the process holds today, and where it lives after this stage:

| In memory today | After |
| --- | --- |
| `Store.orgs`, `projects`, `runs_by_pk`, `active` | rows in `orgs`, `projects`, `runs`, read per request |
| `Project.table` (`ColumnTable`, numpy) | `run_values` and `project_keys` (D77) |
| `Run.summary_flat` (to diff summaries) | the previous summary, read under the run's row lock |
| `Run.keys`, `compacted_keys` | `run_metrics` (D78) |
| `_history` LRU, `PointCounter`, `_rebuilding` | `series_chunks` (D78). No cache |
| `Hub` subscribers | still per replica, fed by `NOTIFY` (D79) |
| `_locks`, `_catalog`, `Budgets._lock`, `Avatars._lock` | row locks and transaction advisory locks (D80) |
| `_deleted_live` | `discarded_runs` table (D80) |
| `Accounts.users`, `keys`, `sessions`, `cli_tokens` | tables already exist. Reads go through a notified cache (D84) |
| `Accounts._codes`, `_grants`, `_wrong_codes` | `auth_codes`, `device_grants`, `auth_failures` tables (D84) |
| `Imports.jobs` and their tasks | `org_imports` rows, claimed with a heartbeat (D81) |
| `Org.rates`, `budgets`, `deleted_projects`, `Run.usage` | tables already exist. Budgets are SQL aggregates |
| background loops (crash detection, S3 sweeps and purges, compaction) | claimed through the database (D81) |
| `files_dir` on one disk | a files backend: a shared volume or S3 (D82) |

What stays in process: the `Hub` (each replica's own SSE connections), the S3 `Upstream` client,
and the D84 auth cache. None of it is the source of anything.

**Latency.** The in-memory design was built to hit 2 to 5 ms. That isn't the goal now. The goal
is to stay inside the budgets 001 set, which were the real requirement:

| Scenario | Today p50 | Budget (unchanged) | Expected after |
| --- | --- | --- | --- |
| `loss` of the newest 50 runs, 500 points each | ~2 ms warm, ~4 ms cold | 10 to 20 ms | 4 to 8 ms (always "cold") |
| Curves of active runs while they stream | ~1 ms | 10 to 20 ms | 3 to 8 ms |
| 6-clause filter and sort, 10k runs × 1.5k columns | ~2.5 ms | 20 to 50 ms | 15 to 40 ms |
| Deleting ~950 of those runs | ~90 ms | 200 ms | under 50 ms (soft delete, D80) |
| Moving 500 of those runs | ~50 ms | 200 ms | 1 to 2 s (see D77, settle in review) |

The latency suite (`tests/app/test_latency.py`) is the check, and Step 9 rebaselines it. If the
filter budget is missed, D77's "if this isn't fast enough" is the fallback.

### D77. The run table: a value index in SQL

The run table is a project's runs × their attributes, `config.*` and `summary.*` keys (D4). It
becomes two tables.

```sql
-- One row per (run, column) that has a value. The query index for filters and sorts.
CREATE TABLE run_values (
    project_pk bigint NOT NULL,  -- denormalized from runs, so a filter is one index range
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    key text NOT NULL,           -- "state", "config.model.layers", "summary.val_loss.min"
    num float8,                  -- numbers, including NaN and ±Infinity
    txt text,                    -- strings
    obj jsonb,                   -- lists and other values (non-finite numbers inside as strings)
    PRIMARY KEY (run_pk, key),
    CHECK (num_nonnulls(num, txt, obj) = 1)
);
CREATE INDEX ON run_values (project_pk, key, num, run_pk) WHERE num IS NOT NULL;
CREATE INDEX ON run_values (project_pk, key, txt, run_pk) WHERE txt IS NOT NULL;
CREATE INDEX ON run_values USING gin (obj jsonb_path_ops) WHERE obj IS NOT NULL;

-- The columns a project has, and their type, for the columns menu and for filter typing.
CREATE TABLE project_keys (
    project_pk bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
    key text NOT NULL,
    int_only boolean NOT NULL,  -- every number in it is integral (D4's display rule)
    runs integer NOT NULL,      -- how many runs have a value; the column goes at 0
    PRIMARY KEY (project_pk, key)
);
```

- **Why this shape, not one `jsonb` per run.** A run with 1,500 flattened keys is a ~50 KB
  document, stored out of line. A filter over 10k of them would read and decompress 500 MB.
  `jsonb` also can't hold `NaN`, which wandb summaries are full of (D3). One row per value turns
  each clause into an index range scan over at most the project's runs, and `float8` holds `NaN`
  and infinities natively.
- **Filters compile to set operations over `run_pk`**, which is what `ColumnTable.filter`'s masks
  are. A leaf clause is one `SELECT run_pk FROM run_values WHERE project_pk = $1 AND key = $2 AND
  …`. `$and` is `INTERSECT`, `$or` is `UNION`, and `$not`, `$nor`, `$ne`, `$nin` are `EXCEPT` from
  the project's runs, so "missing" behaves exactly as an absent mask bit does today. The compiler
  lives in `core/query.py` and replaces `ColumnTable.filter`. `test_table.py` keeps its cases and
  runs them against PostgreSQL, so the semantics can't drift.
- **Operator mapping:**
  - `$eq`, `$in`, `$gt`/`$gte`/`$lt`/`$lte` on numbers use `num`, on strings `txt`. Mixed operands
    split into both, as `table.py` does.
  - `$regex` is PostgreSQL's `~`. Python `re` and POSIX regexes differ in corners (lookarounds,
    `\d` in some locales), so the compiler rejects constructs PostgreSQL doesn't support with
    `FilterError` rather than answer differently. This is a visible change, listed in section 4.
  - `$contains` on lists is `obj @> $value`. `tags` is stored as a list like any other.
  - `$exists` is a membership test on `(run_pk, key)`.
- **Sorts** join `run_values` once per sort key (`LEFT JOIN`, missing last, as today). The default
  is `runs.created_at DESC, runs.pk DESC`, which replaces the row order D4 relied on.
- **The page's cells** come from one query: the visible keys of the page's 50 runs, by primary key.
- **Writes.** `upsert_run` and `file_stream` already know the flattened config and summary. They
  diff against the previous summary (read in the same transaction, under the run's row lock,
  D80) and write only changed keys with `INSERT … ON CONFLICT DO UPDATE` and `DELETE`, in the same
  statement as D3's CTE. A typical step changes a handful of summary keys. `project_keys` is
  upserted only when a key is new to the run or leaves it.
- **Storage.** 10k runs × 1,500 keys is 15M rows, about 2 to 3 GB with indexes, against ~250 MB of
  numpy today. Typical projects have tens to hundreds of keys. This is the main cost of the stage,
  and `pg_total_relation_size` in the latency suite tracks it.
- **Moves** update `run_values.project_pk` for every value of the moved runs. 500 runs × 1,500 keys
  is 750k rows, 1 to 2 seconds. Moves are rare and run in one transaction, so that's accepted
  unless review says otherwise. The alternative (no `project_pk` in `run_values`, filter through
  a join on `runs`) makes every filter slower instead.
- **If this isn't fast enough.** The fallback is a per-replica read cache of the numpy
  `ColumnTable`, keyed by a `projects.version` that every write bumps and `NOTIFY` announces
  (D79). It's correct by construction since PostgreSQL stays the only writer target, but it brings
  back startup cost per project, so it's only built if Step 4's measurements need it.

### D78. Curves: chunks appended with each request

Today an active run's series live only in memory, and a finished run's are compacted into one
blob per metric in `history_columns` (D5). Without memory, the series has to be in PostgreSQL as
soon as the line is, so `history_columns` generalizes into chunks:

```sql
CREATE TABLE series_chunks (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    stream text NOT NULL CHECK (stream IN ('history', 'system')),
    key text NOT NULL,
    first_line integer NOT NULL,  -- the first raw line this chunk covers
    n integer NOT NULL,
    data bytea NOT NULL,          -- D5's encoding: int32 lines (unless dense) || float64 values
    PRIMARY KEY (run_pk, stream, key, first_line)
) WITH (toast_tuple_target = 8160);
ALTER TABLE series_chunks ALTER COLUMN data SET STORAGE EXTERNAL;

CREATE TABLE run_metrics (
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    stream text NOT NULL,
    key text NOT NULL,
    PRIMARY KEY (run_pk, stream, key)
);
```

- **The write path.** `file_stream` already parses the new history and event lines (to append to
  the in-memory series). It now encodes them per metric and inserts one chunk per metric in the
  same CTE as D3. Lines a client resends are dropped before encoding, using the line count read
  under the run's row lock, so chunks never overlap. New metrics go into `run_metrics` with
  `ON CONFLICT DO NOTHING`.
- **Reads** are D5's batched cold load, unchanged in shape: one query for all requested
  `(run, stream, key)`, ordered by `first_line`, concatenated with numpy. Active and finished runs
  take the same path.
- **Compaction** merges a run's chunks for a metric into one, so reads stay one row per curve:
  - when a run finishes (what D5 compacts today);
  - and while it runs, once a metric has more than `COMPACT_AFTER` chunks (64). A run logging
    every second compacts about once a minute per metric, in the background (D81).
  - A compacted run has exactly D5's blob, so `history_columns` rows migrate as single chunks with
    `first_line = 0`. `history_compacted_line_count` and `system_compacted_line_count` go.
- **The raw `history` and `events` lines stay** the source of truth. Chunks are derived and can
  be rebuilt from them, which is what the backfill does for runs that were active during the
  migration.
- **SSE deltas** come from the lines in the request itself, so they need no read (D79).
- `FLOCK_HISTORY_CACHE_POINTS` and `PointCounter` go. PostgreSQL's buffer cache does the job.

### D79. Live events through `LISTEN`/`NOTIFY`

A dashboard on replica B must see a write that landed on replica A.

- **Writers notify inside the write transaction**, so the notification is sent exactly when the
  write commits: `pg_notify('flock_live', '<project_pk> <run_pk> <stream> <from> <to>')` for new
  points, and `'<project_pk> <run_pk> run'` for state or summary changes. Payloads stay far under
  `NOTIFY`'s 8 KB limit because they carry line ranges, not points.
- **Each replica holds one listening connection** (outside the pool, since a pooler in
  transaction mode drops `LISTEN`). On a notification for a run its `Hub` watches, it reads the
  lines `[from, to)` from `history`/`events` once, builds the event, and pushes it to every
  subscriber, exactly as `file_stream` does today. Replicas with no watcher do nothing.
- **Replay and gaps.** D7's protocol already makes this safe: a dashboard subscribes, then replays
  from its line counts, and duplicate points are dropped by line number. If the listening
  connection drops, the replica closes all its streams. Clients reconnect and replay, so a missed
  notification never leaves a dashboard stale.
- **Cost.** `NOTIFY` takes a global lock at commit, which serializes notifying commits. At a few
  hundred active runs that's well under a millisecond each. If it shows in the write-path
  latency, writers notify only for projects that have watchers (a `live_watchers` table each
  replica heartbeats). That's a known gap, not part of this stage.
- **The same channel family** carries cache invalidation (D84) and, if built, D77's table cache.
  It generalizes `s3_proxy/auth.py`'s `s3_invalidate` listener into `core/bus.py`, which the
  proxy then uses too.

### D80. Locks and deletes in PostgreSQL

- **A run's writes** take `SELECT … FROM runs WHERE pk = $1 FOR UPDATE` at the start of the write
  transaction. That replaces `_run_lock`, and also gives the line counts and the previous summary
  that resends and summary diffs need (D77, D78).
- **Catalog changes** (creating, renaming, deleting projects and orgs) lock the org's row, then
  the project's, then the runs' in `pk` order. The same order as today's `_catalog`, `_locked` and
  `_org_locked`, so the same no-deadlock argument holds.
- **Budget and avatar changes** use `pg_advisory_xact_lock` on a key per org instead of a
  process-wide `asyncio.Lock`.
- **Deletes become two-phase.** Deleting runs, a project or an org sets `deleted_at` and returns.
  Every query filters `deleted_at IS NULL` (a partial index on `runs (project_pk) WHERE
  deleted_at IS NULL` keeps that cheap). A background purge (D81) then deletes the rows in
  batches, and their files. D15's "deletes are hard" still holds from the user's side: nothing
  is restorable, and the API can't see them.
- **Runs deleted while a client was logging** (D15) go in `discarded_runs (entity, project, name,
  last_seen)` instead of `_deleted_live`. `file_stream` checks it when the run isn't found, and
  updates `last_seen`. The purge drops rows quiet for `FLOCK_CRASH_TIMEOUT`.

### D81. Background work, claimed through the database

Every loop that runs in the process today, and how it runs on N replicas:

| Work | How |
| --- | --- |
| Crash detection (`mark_crashed_runs`) | one `UPDATE … WHERE heartbeat_at < … RETURNING`, safe on every replica; the replica that wins the update sends the events |
| Compaction (D78) | `runs` that need it, claimed with `FOR UPDATE SKIP LOCKED`, one at a time |
| Purging deletes (D80), S3 key sweep, expired auth rows | idempotent batched `DELETE`s, on every replica |
| S3 bucket purge (`_purge_loop`) | deleted, unpurged `s3_buckets` rows claimed with `SKIP LOCKED`. A delete on any replica wakes every replica's loop through `NOTIFY` instead of the in-process `_purge_wanted` event, with `PURGE_RETRY` as the fallback poll |
| wandb imports (D37) | the starting replica claims the `org_imports` row and heartbeats it every 10 s; another replica marks a row `interrupted` once its heartbeat is a minute old. Cancelling sets `cancel_requested`, which the running replica checks between runs |
| Gravatar fetches | fire-and-forget on the replica that took the request, as today. A lost fetch only means no picture |
| Orphaned run files (startup `sweep`) | part of the purge, no longer at startup |

No leader election. Each job is either idempotent or claimed per row, so any number of replicas
can run every loop.

### D82. Run files on shared storage

Run files (D3, `save_file`, `file_path`) are written under `FLOCK_FILES_DIR` on one disk, which
only one replica can see. A small `FileStore` interface (`put`, `get` as a stream, `delete_run`)
gets two backends, chosen by `FLOCK_FILES_BACKEND`:

- **`local`** (the default): today's directory. Correct for one replica, or for several on a
  `ReadWriteMany` volume such as CephFS, since writes are already a temp file plus rename.
- **`s3`**: a bucket on the upstream S3 server the proxy uses (`FLOCK_FILES_BUCKET`), with keys
  `runs/<run_pk>/<name>`, through the same `Upstream` client. Uploads keep going through Flock's
  signed URLs, so the wandb SDK sees no change.

Moving existing files is a script (`scripts/migrate_files.py`) that copies the directory into the
bucket. It's safe to run while the server serves, since reads fall back to the directory until it
finishes.

### D83. Deployment

- **Replicas are stateless.** Startup is "connect, check the schema, listen", a second or less,
  whatever the data size. `Database.SERVER_LOCK`, `gc.freeze()` and the startup load go.
- **Migrations run once per deploy**, not per replica: a Kubernetes Job (or init container) runs
  `python -m app.api.src.db.migrate`, and replicas run with `FLOCK_MIGRATE_ON_STARTUP=false`. The
  migration lock (`Migrations.LOCK`) already makes concurrent runs safe, so the compose default
  stays on.
- **Probes.** `/healthz` stays a liveness check that touches nothing. A new `/readyz` checks the
  pool and the listening connection, so a replica whose `LISTEN` is down leaves the load balancer
  until it reconnects.
- **Shutdown.** On `SIGTERM` a replica fails `/readyz`, closes its SSE streams (as today), and
  finishes in-flight writes. `terminationGracePeriodSeconds` of 30 is enough.
- **No sticky sessions.** SSE reconnects to any replica and replays (D79). Uploads go through
  signed URLs that any replica can verify, since the secret is in `meta`.
- **Connections.** Each replica uses its pool plus one listening connection. With a pooler such
  as PgBouncer in front, the listening connection must bypass it (`FLOCK_LISTEN_DATABASE_URL`,
  defaulting to `FLOCK_DATABASE_URL`).
- **compose.yaml** drops the "exactly one replica" comment and the `files` volume becomes
  optional. Its default stays one replica with the local files backend.

### D84. Auth without the in-memory account store

D20's "authenticating never waits for the database" was a property of the in-memory store. It
becomes a cache in front of PostgreSQL, built like the S3 proxy's (`s3_proxy/auth.py`):

- Lookups (a key or token hash → user, a session pk → alive, a user → roles) are cached for 30
  seconds per replica.
- Any change to users, memberships, keys or sessions sends `NOTIFY flock_auth`, and every replica
  drops its cache. While the listening connection is down, nothing is cached, so a revoked
  session or a changed role still applies at once, as D19 and D47 promise.
- A miss is a primary-key lookup, about 0.3 ms. Hashing and the argon2id worker thread are
  unchanged.
- **Short-lived state gets tables:** `auth_codes` (Google sign-in's one-time codes),
  `device_grants` (D46, which today vanish on restart, so a `flock login` in progress fails), and
  `auth_failures` (the wrong-code rate limit, which today is per replica and so would multiply by
  N). Each is a single-row insert, read or delete, and the D81 sweep removes expired rows.
- **The signup lock** (`_signup`, so only one first account becomes super admin) becomes a
  transaction advisory lock.

---

## 3. Plan

A stack of PRs, each leaving Flock working as a single replica, with the full test suite
passing. The server lock goes only in the last code step, once nothing in memory is a source
of truth. Branches: `feat/pg-bus` first, each next one branched from the previous.

### Step 1. The bus, and the data model

- `core/bus.py`: the listening connection with reconnect, channel callbacks, and the "drop
  everything while disconnected" rule. `s3_proxy/auth.py` moves onto it.
- Migration `0013_postgres_only.sql`: `run_values`, `project_keys`, `series_chunks`,
  `run_metrics`, `discarded_runs`, `auth_codes`, `device_grants`, `auth_failures`,
  `runs.deleted_at`, `projects.deleted_at`, `orgs.deleted_at`, the `org_imports` heartbeat
  columns. `history_columns` rows are copied into `series_chunks` in SQL.
- `scripts/backfill.py`: fills `run_values`, `project_keys` and `run_metrics` from `runs.config`
  and `runs.summary` (which need Python's lenient JSON for `NaN`), and builds chunks for runs whose
  series weren't compacted. Resumable by run pk, and run by the migration Job.

### Step 2. Writes go to the new tables

`upsert_run`, `file_stream`, `import_run`, `edit_runs` and `move_runs` also write `run_values`,
`series_chunks` and `run_metrics`, and notify (D79). Reads still come from memory, so this step
changes no behavior. A test compares the two after every write in the existing suites, which is
the check that D77 and D78 hold the same data as memory.

### Step 3. Auth from PostgreSQL

`Accounts` reads through the D84 cache. Codes, device grants and failures move to their tables.
`test_auth.py`, `test_device.py`, `test_cli_sessions.py` and `test_credentials.py` pass unchanged.

### Step 4. The run table from PostgreSQL

- `core/query.py`: the D77 compiler, and the page and cell queries.
- `api/runs.py` and `api/dashboard.py` read through it. `ColumnTable` stays only as the test oracle.
- `test_table.py` runs each case against both. `test_query_api.py` passes unchanged.
- Measure the filter rows of the latency suite here. If they miss the budget, build D77's fallback
  cache before going on.

### Step 5. Curves from PostgreSQL

`histories()` reads `series_chunks`. Compaction runs as D81 work. The LRU, `PointCounter` and
`FLOCK_HISTORY_CACHE_POINTS` go. `test_history.py` and `test_system_metrics.py` pass.

### Step 6. Live events across replicas

`file_stream` stops pushing to the `Hub` directly. Every replica's `Hub` is fed by `flock_live`.

### Step 7. The catalog, locks, deletes and background work

- `Store.orgs`, `projects`, `runs_by_pk` and `active` go. Lookups are queries.
- D80's locks and two-phase deletes, `discarded_runs`, and D81's loops.
- `Budgets` and `Feed` become SQL aggregates over `gpu_usage` and `runs`.
- Imports claim and heartbeat their row.

### Step 8. Files, and removing the server lock

- `FileStore` with the `local` and `s3` backends (D82), and `scripts/migrate_files.py`.
- `SERVER_LOCK`, the startup load, `gc.freeze()` and the dead in-memory code go.
- `/readyz`, `FLOCK_LISTEN_DATABASE_URL`, `FLOCK_FILES_BACKEND`, `FLOCK_FILES_BUCKET`.

### Step 9. Tests

- **Two replicas.** The `FlockServer` harness gains a `replicas=2` mode: two app instances on one
  database, with requests spread across them. Run under it:
  - the wandb integration test, logging through one replica while a dashboard streams from the
    other;
  - deleting a run on one replica while its client logs through the other;
  - revoking a session on one replica and using it on the other at once;
  - `flock login` approved on one replica and polled on the other;
  - a replica killed mid-import, with the import marked interrupted by the other.
- **Restart.** Kill a replica mid-request; nothing it acknowledged is lost, and nothing it didn't
  is half-applied.
- **Filters.** The `test_table.py` cases against PostgreSQL, plus `$regex` constructs that are now
  refused.
- **Latency.** The suite runs against the new paths, with the table in D76 as its budgets, and
  reports `run_values` size.

### Step 10. Docs and status

- Rewrite `docs/source/how-it-works.md`: the "one process" and "hot in memory" sections become
  "stateless replicas" and "chunks", with the new numbers.
- `docs/source/deploying.md`: the Kubernetes shape of D83, the migration Job, the files backends.
- `docs/source/configuration.md`: the new settings, and `FLOCK_HISTORY_CACHE_POINTS` removed.
- The README's "keeps the data it serves in memory" paragraph.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Known gaps after this stage

- **`$regex` is PostgreSQL's, not Python's.** Patterns PostgreSQL reads differently are refused
  with a 400 instead of matching as before. `\b` is the likely one.
- **Every query goes to the primary.** Sending dashboard reads to a read replica needs the SSE
  replay to tolerate replica lag. Later, if the primary is the bottleneck.
- **`NOTIFY` on every write** (D79). Fine at hundreds of active runs. Thousands may need the
  per-project interest table.
- **`run_values` is large** for projects with thousands of columns (D77). Partitioning by
  `project_pk`, or dropping `summary.*` keys nobody filters on, are the levers if it matters.
- **Moves take seconds** for hundreds of wide runs (D77): about 3 s for 500 runs of 1.5k
  columns, over the 2 s accepted in review.
- **No Helm chart.** D83 describes what one needs.

## 5. To settle in review

1. **The latency targets** in D76. Are the 001 budgets still the bar, or is "within 2× of today"?
2. **Moves at 1 to 2 seconds** (D77), or keep them fast and make every filter join `runs`?
3. **`$regex` refusing patterns** PostgreSQL can't run, rather than changing their meaning.
4. **The files backend.** CephFS behind `local`, or the S3 backend, for the Kubernetes deploy?
5. **The D77 fallback cache.** Build it only if Step 4 misses the budget (as written), or not at
   all, and relax the budget instead?

Settled in review: the 001 budgets stay the bar (the D76 Budget column); moves at 1 to 2 seconds,
keeping `project_pk` in `run_values`; `$regex` refuses what PostgreSQL can't run; both files
backends, `local` the default; the fallback cache only if the filter rows miss.

---

## 6. How it was actually built

The plan was followed in twelve commits: one per step, except that step 3 has two (auth, then
the budget and feed queries) and steps 5 and 6 share one (curves and the relay both read
`series_chunks`, and neither worked without the other), then the latency work and the docs. These are the places where the code differs from the
decisions above, or adds to them.

### The run table (D77)

- **Column counts go through a change log.** Writers upserting `project_keys` directly serialized
  every write of a project on its hot rows. Writers append to `project_key_changes` instead, and
  background work folds the log into `project_keys` (`values.fold`, claimed with `SKIP LOCKED`).
  Readers add the two (`values.COLUMNS`), so counts are exact at once.
- **No GIN index.** The planner never chose it over `(project_pk, key)`, whose estimate is a
  handful of rows. Lists are found through `run_values_lists`, a btree on `(project_pk, key,
  run_pk)` that includes the list itself, so `tags contains baseline` reads no table rows (6 ms
  to 1 ms over 10k runs).
- **Long values.** An index entry holds about 2.7 kB, and a summary string of 8 kB failed the
  write. Strings and lists are indexed with their value only when short
  (`run_values_short_text`, `run_values_short_list`, in 0013). The long ones have their own
  index without the value, and every string or list clause reads both. A test covers values of
  11 kB and lists of 200 elements. Keys still have to fit an index entry (about 2.6 kB).
- **Kind pruning.** A page query first reads which kinds of values each filtered and sorted
  column holds (`KEY_KINDS`, from the counts). Clauses on kinds a column doesn't hold are left
  out, and columns of only numbers or only strings sort with fewer terms. The counts are read
  apart from the page, so a value of another kind written in between is left out of that one
  answer.
- **Two-phase pages.** The order is found from run pks alone, then the page's rows are joined, so
  sorting thousands of runs doesn't carry their rows. Sort joins include `project_pk` so they can
  read the key's index.
- **The `mixed` kind.** A column with several kinds of objects (lists and booleans, say) reports
  `mixed`, as one with numbers and strings does. `ColumnTable` reported the first kind it met.
- **Operands are checked even on columns no run has** (`$gt` with a list is a 400 either way).
- **Regexes** match with `COLLATE "default"`, so character classes follow the database's locale
  as Python's follow Unicode. `check_regex` refuses what PostgreSQL reads differently or not at
  all: `\b` and `\B` (word boundaries to Python, a backspace and an escape to PostgreSQL), a few
  other escapes (`\N`, `\G`, `\z`, ...), named and atomic groups, conditionals, possessive
  quantifiers, and inline flags other than a leading `(?i)`. Lookarounds and backreferences run.
- **NaN sorts with missing values**, last in both directions, as numpy did. It never matches a
  comparison.
- **`int_only`** is sticky: a column that held a float shows floats until no run has a value
  (its count reaches 0), when it starts afresh.
- **The fallback cache wasn't built.** After the work above, every filter row is inside its
  budget (see the latency table below). The `$or` row has little headroom.

### Curves and live events (D78, D79)

- **A query per key set.** `Store.histories` reads only the asked keys' chunks (`CHUNKS`) when
  keys are given, and the run's metric names through `run_metrics` only when they aren't.
- **Compaction** is two statements (delete, then insert), since one CTE hit the unique key.

### Locks, deletes and background work (D80, D81)

- **Small deletes are purged inline.** Deletes of up to 100 runs purge in the request, after the
  commit. Larger ones are marked and left to background work (`PURGE_BATCH` runs per round).
- **Run files** of purged runs are removed by the purge, and files of runs that no longer exist
  are swept hourly by background work, instead of at startup.
- **Avatars** are rendered under one advisory lock, not one per org.
- **Imports** are claimed through `org_imports` with a heartbeat (`HEARTBEAT`, `STALE_AFTER`). A
  cancel on another replica sets `cancel_requested` and the state at once; the importing replica
  stops at its next heartbeat.
- **The S3 bucket purge** holds its claim's transaction open while it purges the bucket upstream.
- **Bulk loads.** After a round folds 100,000 count changes or more, background work runs `VACUUM
  (ANALYZE)` on the run table, the counts, `runs` and `gpu_usage`. Without fresh statistics and
  visibility maps, filters and the budget panel read every run row by row for minutes, until
  autovacuum came by.

### Auth, budgets and the feed (D76, D84)

- **Budgets and the home feed** moved to SQL in steps 3 and 4, with auth, since the in-memory
  usage counters were written by the same code. The budget panel reads its four queries at once.
- **Device sign-ins** live in `device_grants`, so they survive restarts, which they didn't.
- **CLI session use** is kept per replica and written at most every `CLI_WRITE_INTERVAL`, as
  before. A replica lists the uses it saw, and the others' once written.
- **Revoking a session** is refused on other replicas as soon as they hear the notification, a
  few milliseconds. A request already past its check finishes.

### Migration, deployment and tests (D83)

- **The backfill** runs at startup when the server applies 0013 itself, as well as from
  `scripts/backfill.py`. It folds the counts after each batch.
- **The test harness** starts replicas with `make_flock.replicas(2)`, which returns them with a
  client that sends each request to the next one. `FlockServer.kill` stops a replica without
  draining it.
- **Tests that changed**, each for a behavior this design changes:
  - the history cache and eviction tests became chunk and compaction tests
    (`test_finished_runs_are_compacted_into_one_chunk_per_metric`,
    `test_active_runs_compact_once_they_have_many_chunks`), and compacted-state assertions in
    `test_system_metrics.py`, `test_migrations.py`, `test_demo_seeding.py` and
    `test_wandb_import.py` check chunks;
  - `test_only_one_server_per_database` became `test_servers_share_a_database`;
  - `test_delete_while_reading_leaves_nothing_cached` became `test_delete_while_reading`;
  - tests of deleted rows and files call `flush` first (two-phase deletes);
  - the slow-subscriber test waits for events, which now arrive through notifications;
  - store lookups are awaited (`get_run`, `orgs`, `project_info`, ...);
  - in the latency suite, the cold test no longer evicts a cache, and the move budget is 2 s.
- **Tests added**: `tests/app/test_replicas.py` (7), `tests/s3/test_files_backend.py` (2), the
  PostgreSQL-against-`ColumnTable` oracle and regex tests in `test_table.py`, the long-values
  test in `test_query_api.py`, and the compaction tests above.

### Latency

Measured on an Apple Silicon laptop with PostgreSQL 18 in Docker on tmpfs, end to end over HTTP,
as in 001. `run_values` holds 15.1M rows, 3.8 GB with its indexes, for the 10k × 1.5k sweep.

| Scenario | Before (memory) | p50 | p95 | Budget |
| --- | --- | --- | --- | --- |
| `loss` of the newest 50 runs, 500 points each | ~2 ms | 4.8 ms | 5.7 ms | 10 to 20 ms |
| The same, every read from PostgreSQL | ~4 ms | 5.0 ms | 5.6 ms | 10 to 20 ms |
| `loss`, `val_loss` and `lr` of the newest 50 runs | ~4.5 ms | **12.3 ms** | 14.9 ms | 10 to 20 ms |
| Curves of active runs while they stream | ~1 ms | 5.1 ms | 8.0 ms | 10 to 20 ms |
| One-metric filter, 10k runs × 1.5k columns | ~0.5 ms | 4.2 ms | 4.8 ms | 20 to 50 ms |
| 6-clause filter and sort | ~2.5 ms | 13.0 ms | 15.2 ms | 20 to 50 ms |
| `$or`, a regex and a string `$in`, sorted by a string | ~3 ms | 19.0 ms | 21.0 ms | 20 to 50 ms |
| All matching ids (7k runs) | | 13.2 ms | 16.3 ms | 20 to 50 ms |
| 100 columns of 100 runs, sorted | | 15.6 ms | 18.3 ms | 20 to 50 ms |
| Column search | | 3.5 ms | 3.8 ms | 20 to 50 ms |
| Home feed, first page and a filtered later page | ~0.6 ms | 11.2 ms, 5.0 ms | 12.3 ms | 20 to 50 ms |
| Budget panel, 10k ledger entries | ~3 ms | 12.5 ms | 14.2 ms | 20 to 50 ms |
| Deleting about 950 runs | ~90 ms | 19 ms | | 200 ms |
| Moving 500 runs | ~50 ms | **3.1 s** | | 1 to 2 s (review) |

Two rows miss. Several metrics of the newest 50 runs is 1.5 MB of JSON: building and decoding it
takes about 4.5 ms, as before, and selecting the runs and reading their ~500 chunks (the 8
running ones aren't compacted yet) adds about 3 ms. Moving 500 runs of 1.5k
columns rewrites 750k rows of `run_values` with their index entries, about 3 s on this laptop
against the 1 to 2 s accepted in review.
