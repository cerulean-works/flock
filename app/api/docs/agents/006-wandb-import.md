# 006: importing a W&B entity into an organization

| | |
| --- | --- |
| Status | applied (API); flock-ui pages pending |
| Date | 2026-10-19 |
| Builds on | [003](003-organizations-and-auth.md), [004](004-home-and-budgets.md) |
| Keeps | D2 (one server per database, small state in memory), D18 (orgs are wandb entities), D25–D29 (usage ledger) |
| Result | 209 tests in `tests/app/` pass; see [section 4](#4-how-it-was-actually-built) |

## 1. Goal

Teams moving to Flock leave their history in W&B. Orgs are already wandb entities (D18),
so the on-ramp is: when a super admin creates an org, they can name a W&B entity and give an
API key, and Flock copies every project and run of that entity into the new org.

1. **On creation.** `POST /orgs` takes an optional `wandb: {entity, api_key, base_url?}`.
   A bad key or entity is refused before the org exists.
2. **In the background.** The import runs as a task; its progress is readable at
   `GET /orgs/{org}/import`, and it can be cancelled, resumed, or started for an existing org.
3. **Faithful copies.** Runs keep their id, timestamps, config, summary, tags and metadata,
   with their full metric history, system metrics, console log and small text files.

**Out of scope:**
- media, tables, checkpoints and other run files; artifacts; reports; sweeps;
- updating runs already copied (a re-import skips them);
- continuing an import after a restart without the key (it is never stored);
- the flock-ui pages (the setup and admin forms, the progress card), a later stage.

---

## 2. Design decisions

The numbering continues from 005.

### D35. W&B is read over GraphQL with httpx, not with the wandb SDK

The SDK's public API runs a separate Go process (wandb-core), is synchronous, and fetches
lazily: a request per run per attribute unless `lazy=False`. Its importer module
(`wandb.apis.importers`) was removed in recent releases. The import needs six queries, so
`core/wandb_source.py` sends them itself with `httpx.AsyncClient`:

| Query | Reads |
|---|---|
| `FlockViewer` | the key works (`viewer`), and the entity exists |
| `FlockMembers` | members' emails by username, for owners (D38); optional |
| `FlockProjects` | `models(entityName:)`, paginated, with `runCount` |
| `FlockRuns` | `project.runs(order: "+created_at")`, paginated, every field in one query |
| `FlockHistory` | `history(minStep, maxStep, samples)` in windows of 1000 steps up to `historyKeys.lastStep` |
| `FlockFiles` / `FlockEvents` | the light files by name; sampled `events` only when there is no `wandb-events.jsonl` |

Rate limits (429), server errors and dropped connections are retried with exponential
backoff, honouring `Retry-After`. The pre-check (`FlockViewer`) isn't retried, since someone
is waiting on it. File downloads only send the key to the W&B server itself, never to the
storage its signed `directUrl`s point at. httpx's per-request INFO logs are silenced, since
they would log those signed URLs.

### D36. Imported runs are written straight to their final, compacted form

The live path (`_create_run`, `file_stream`) stamps `time.time()` and accrues usage only
between heartbeats, so replaying a run through it would lose its times and bill nothing.
`Store.import_run` inserts the run, its `history`, `events` and `logs` lines, its
`history_columns` blobs (with compacted counts equal to the line counts), its `gpu_usage`
ledger rows and its `run_files` in one transaction, modelled on `scripts/seed_demo.py`. Then
it registers the run in memory as `_load` does and re-sorts the project's rows by
`(created_at, pk)`. Nothing is left to rebuild at startup.

- **Locking.** It holds the project's lock for one run at a time, so live logging, edits and
  deletes in the org aren't blocked for the length of an import. Fetching from W&B happens
  outside any lock, up to 4 runs at once.
- **Idempotent.** A run whose id the project already has is skipped (`None`). That is what
  makes resuming work (D37).
- **Files** are written before the commit. If the commit fails, the startup sweep removes the
  directory of a run that doesn't exist.
- **States.** Imported runs are never active (their clients log to W&B): `finished` stays,
  `failed` and `killed` become `failed`, `crashed` stays, anything else becomes `crashed`.
- **Usage.** The ledger gets the time from `createdAt` to `heartbeatAt`, split by month, with
  the GPUs read from `wandb-metadata.json`. Imported runs count towards past months' spend.
- **Projects** keep W&B's `createdAt`: `_insert_project` takes an optional `created_at`.

### D37. Progress is persisted, the key is not

Migration `0007_org_imports.sql` adds `org_imports`, one row per org (its latest import):
entity, base URL, state, counts, current project, last error, who started it and when.

- **The API key lives only in the running task.** At startup, rows still `running` become
  `interrupted`. Resuming is starting again with the key; copied runs are skipped (D36).
- **Progress** is kept in memory (`Imports.jobs`) for `GET`, and saved at most once a second
  while running, and on every end.
- **States:** `running`, `finished`, `failed` (a fatal error, such as the key being revoked),
  `cancelled` (`DELETE /orgs/{org}/import`, or the org being deleted) and `interrupted`
  (shutdown).
- **A run that fails** (a GraphQL error, a download that keeps failing) is counted in
  `runs_failed`, its error kept in `error`, and the import goes on. Auth errors and the org
  or project disappearing stop the whole import.
- **Deleting the org** cancels its import while holding the org's locks, so the task stops
  before writing anything more, then the row goes with the org.

### D38. Owners are matched by email

A run belongs to the Flock user whose email matches its W&B author's, read from the entity's
`members { username email }`. W&B shows those only to some keys (a team admin's), so the
query is optional. Runs with no match belong to the super admin who started the import.

### D39. The API

| Endpoint | Who | Meaning |
|---|---|---|
| `POST /orgs` with `wandb` | super admins | Checks the key and entity (400, 404 or 502 before creating anything), creates the org, starts the import; the 201 adds `import` |
| `GET /orgs/{org}/import` | org admins | Progress, 404 if never imported |
| `POST /orgs/{org}/import` | super admins | Start or resume, 202; 409 while running |
| `DELETE /orgs/{org}/import` | super admins | Cancel; the runs copied stay |

`FLOCK_WANDB_BASE_URL` (default `https://api.wandb.ai`) is used when `base_url` is left out.

### D40. Known gaps

- Runs copied once aren't refreshed by later imports.
- A run with very long history is held in memory while it is copied (its rows and series).
  The parquet export (`parquetHistory`) would stream faster; it isn't used yet.
- `runs_total` comes from W&B's `runCount` and is corrected as runs are listed, so it can move
  during an import.

---

## 3. Plan

1. Migration `0007_org_imports.sql` and the `wandb_base_url` setting (with compose
   passthrough).
2. `Store.import_project` and `Store.import_run` (D36); `file_parts` shared with
   `file_path`.
3. `core/wandb_source.py` (D35) and `core/imports.py` (`Imports`, `ImportJob`, D37–D38),
   wired into `Store.open`, `close` and `delete_org`.
4. The API in `api/orgs.py` (D39).
5. `tests/app/test_wandb_import.py` against a fake W&B GraphQL and file server.
6. Docs: the guide page `importing-from-wandb.md`, `api/orgs.md`, `configuration.md`,
   `errors.md`.

## 4. How it was actually built

As planned. The tests run against `FakeWandb`, a local HTTP server answering the six
queries by operation name and serving files, with two runs per page so pagination is
exercised. They cover the full copy (times, states, owners, curves, system metrics, logs,
files, GPUs and compaction), the pre-checks, failed runs and resuming, cancelling and
deleting the org mid-import, and a restart leaving an import interrupted.

The importer hasn't been run against wandb.ai itself. The query shapes follow the wandb SDK's
own (`wandb/apis/public`); check them against a real entity before relying on them.
