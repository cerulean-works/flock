# 001: wandb-compatible tracker

| | |
| --- | --- |
| Status | applied (checkpoint) |
| Date | 2026-10-17 |
| Builds on | nothing (first stage) |
| Result | `flock/tracker/` as of this checkpoint; 104 tests in `tests/tracker/` pass |

## How to use this folder

Each `NNN-name.md` file here works like a database migration, but for the codebase. It
records one stage of the project: what the stage set out to do, the decisions it made and why,
and an ordered plan that takes the code from the previous stage to this one. Applying
`001`, `002`, ... in order rebuilds the project and the reasoning behind it.

Treat applied stages as immutable. When the design changes, write the next numbered file and
say which earlier decisions it replaces; don't rewrite history here. The README describes the
current behavior. These files explain how it got that way.

This stage is retroactive. The plan in [section 4](#4-plan) is the order that should be
followed to rebuild the project, which is cleaner than the order it was actually built in.
[Section 5](#5-how-it-was-actually-built) records the actual path, because some of the
lessons came from detours.

---

## 1. Goal

Build an in-house server that the unmodified `wandb` Python SDK (0.31, which uses the
`wandb-core` Go service) can log to, and that a dashboard can read from very quickly.

**In scope:** `wandb login`, `wandb.init` (including `resume` and `id`), per-step
`run.log`, `run.summary`, `run.config`, tags and notes, exit codes, crashes, console
output, run files, offline runs with `wandb sync`, and the SDK's system metrics. Viewing and
filtering of runs.

**Out of scope:** artifacts, sweeps, reports, wandb's own web UI, teams and organizations,
and fine-grained permissions.

**Latency budgets.** These are the requirements that shape everything else. They are
measured end to end: HTTP request, server work, and client-side response decoding.

| Scenario | Budget | At this checkpoint (p50) |
| --- | --- | --- |
| Loss curves of the newest 50 runs, 5k steps logged every 10 (500 points each) | 10–20 ms | ~2 ms (cold ~4 ms, p95 5.6 ms) |
| Loss curves of every ongoing run, while those runs are logging | 10–20 ms | ~1 ms |
| Several metrics for the newest 50 runs | 10–20 ms | ~4.5–5.4 ms |
| Column filter + sort over 10k runs × 1.5k columns | 20–50 ms | 0.5–3.3 ms |

**Resource stance.** It's an in-house tool, so the server may spend more RAM and disk to
get lower latency. One server process per deployment is acceptable.

---

## 2. Design decisions

Each decision gives the choice, the reason for it, and what it costs. Later stages that
overturn one of these should cite its ID (for example, "replaces D2").

### D1. Implement the wire protocol, not a wandb fork

The SDK stays untouched. Clients only set `WANDB_BASE_URL` and a key. The server implements
exactly the GraphQL operations and HTTP endpoints that the SDK calls.

- **There's no general GraphQL engine.** Operations are dispatched by `operationName`, with
  two fallbacks: the name in the query text, then a known root field (`upsertBucket`,
  `viewer`, ...). Each handler returns the fields the SDK selects.
- **Feature probes get fixed answers** (`ServerFeaturesQuery`, `XXHashDigestsEnabled`,
  organization lookups, ...).
- **Unknown operations return a GraphQL error** rather than a guessed answer, so an
  unsupported SDK feature fails loudly instead of corrupting state.
- **Capture before you implement.** Record what the real SDK sends, using a stub server
  that logs every request. The Go service's behavior differs from what the Python SDK
  source suggests. [Section 3](#3-wandb-protocol-facts) lists what was learned.

### D2. Keep the working set in one process's memory, with PostgreSQL for durability

All reads are answered from memory: run metadata, the full config and summary table, and the
series of active runs and recently read runs. PostgreSQL is the source of truth, and the
server loads from it at startup.

- **Why:** the latency budgets leave no room for a query planner. A filter over 10k runs is
  a handful of numpy passes over arrays of 10k elements, which takes microseconds per
  clause.
- **Cost: one server per database.** The server takes a PostgreSQL advisory lock (session
  level, `Database.SERVER_LOCK`), so a second instance refuses to start rather than letting
  two caches diverge.
- **Cost: startup time** grows with the number of runs (a few seconds for 10k runs × 1.5k
  columns).
- **Cost: RAM** grows with runs × columns.
- **GC pauses:** after loading, `gc.freeze()` moves the millions of long-lived objects out
  of the cyclic GC's view, so full collections don't stall requests.
- **History:** the PoC used SQLite, and the user asked to switch to PostgreSQL. The
  in-memory design didn't change. Only the durability layer and the deployment shape did.

### D3. Store raw lines as text, idempotently, one statement per request

- **Raw wandb JSON is stored as `text`, not `jsonb`.** This covers config, summary, and
  history and event lines. wandb writes `NaN` and `Infinity` literals, which `jsonb`
  rejects. Parse with orjson and fall back to the stdlib `json` module for those literals
  (`utils.loads_lenient`). PostgreSQL `text` can't hold `\x00`, so strip it.
- **Line tables are keyed by `(run_pk, line)` and written with upserts.** These are
  `history`, `events` and `logs`. The SDK addresses lines by offset and re-sends chunks it
  couldn't confirm, so a retry must be a no-op.
- **One `file_stream` request is one SQL statement.** Data-modifying CTEs upsert all three
  line kinds and update the `runs` row in a single round trip and transaction. The request
  is acknowledged only after that commits.
- **Load a run's series before persisting new lines.** Otherwise a cache rebuild from the
  database could see the new lines and then append them a second time. This was a real bug,
  found on resume and covered by a regression test.

### D4. Run table: columnar numpy arrays with Mongo-style filters

- **Layout:** each project has a `ColumnTable`. Each column has a float64 array, an object
  array for non-numeric values, and a presence mask. The columns are the run attributes plus
  the flattened `config.*` and `summary.*` keys (dot-joined; values with a `_type` key, such
  as media and histograms, stay whole).
- **Filters** use the syntax that `wandb.Api().runs(filters=...)` uses. They compile to
  vectorized numpy, so no database access happens.
- **Semantics:** a missing value never satisfies a comparison and does satisfy `$ne`. On
  list columns such as tags, `$eq` and `$in` test membership.
- **Remove a column once it has no values left**, so the column lists stay accurate.
- **Responses are column-oriented** (`ids` plus `data[column]`), so the encoder serializes
  numpy arrays directly instead of building a dict per run.

### D5. Series: hot in memory, cold as compacted per-metric blobs

- **In memory:** a run's series is `History`, one `Series` per metric holding parallel
  arrays of row indices (int32) and values (float64). Rows are line numbers. `_step`,
  `_runtime` and `_timestamp` are just series too, so a chart can use any of them, or any
  logged metric, as its x-axis.
- **Cache:** an LRU keyed by `(run_pk, stream)` and bounded by a total point budget
  (`history_cache_points`, counted in O(1) by `PointCounter`). Active runs are always
  cached. Eviction runs after inserts and after compaction.
- **Compaction:** when a run stops being active, each of its streams is compacted into one
  `bytea` blob per metric in `history_columns`. A blob holds n float64 values, preceded by n
  int32 row numbers unless the metric was logged on every line ("dense elision").
  `runs.{stream}_compacted_line_count` records how many lines the blobs cover. If the
  run has more lines than that, the blobs are stale and are rebuilt from the line table.
- **PostgreSQL storage tuning** brought cold-curve p95 down from ~27 ms to ~5.6 ms:
  - `toast_tuple_target = 8160` keeps curves up to about 1k points inline in the heap page.
  - `STORAGE EXTERNAL` stores larger ones out of line without pglz, because float arrays
    barely compress.
- **Batched cold loads:** cold runs load in one query per request
  (`run_pk = ANY($1) AND key = ANY($2)`), however many runs are asked for.
- **Single-flight rebuilds:** concurrent requests for the same run's series wait for a
  single rebuild instead of each starting one.

### D6. Two streams: logged history and system metrics

- **What wandb does:** the SDK's `wandb-symon` samples CPU, memory, disk, network and GPUs
  every `x_stats_sampling_interval` (15 s by default). It sends these samples as
  `wandb-events.jsonl` lines: flat `system.*` keys plus `_runtime`, `_timestamp` and
  `_wandb: true`, with no `_step`.
- **Same machinery:** both streams go through D5, as `STREAMS = {"history": "history",
  "system": "events"}`, which maps each stream to its line table.
- **Default x-axis:** `_step` for history and `_runtime` for system.
- **Opt-in everywhere:** the read API takes `stream=system`. The snapshot and the live
  stream add system data only when called with `system=true`, so existing clients get
  byte-identical responses.
- **Never mix the streams:** keeping system keys out of `history_keys` is a tested invariant.

### D7. Dashboard transport: a binary snapshot plus SSE with replay cursors

- **`GET .../workspace`** returns one binary `FLK2` blob with everything a page shows:
  - the runs table with every column, numeric cells as column-major f64;
  - every metric's curve for each run on the page, downsampled to fit a total point budget,
    with y values as f32;
  - x values omitted for evenly spaced series, which is the usual case for `_step`.

  The client decodes it into typed arrays without parsing, so 100 runs × 1.5k columns stays a
  few MB. The README documents the exact layout.
- **`GET .../stream`** sends Server-Sent Events: `hello`, `history`, `system`, `run` and
  `created`.
- **Replay cursors:** the snapshot reports each run's line counts. The client passes them
  back as `runs=id:lines[:system_lines]`, and the stream first replays everything logged
  since then. Snapshot → subscribe therefore never loses points.
- **Back-pressure:** each subscriber has a bounded queue (`Subscriber.QUEUE_SIZE = 512`). A
  slow subscriber is disconnected and reconnects with its latest cursor, instead of
  buffering without limit.

### D8. Projects and auth are minimal on purpose

This was a deliberate scope cut. Later stages should treat it as debt, not as a design.

- **Projects:** a project is the text pair `(entity, project)` on each run. There's no
  projects or entities table, and a project comes into existence with its first run.
- **Keys:** auth is `TRACKER_API_KEYS=user:key,...`, held in memory. With no keys
  configured, any key is accepted and everything lands under `TRACKER_DEFAULT_ENTITY`.
- **Accepted credentials:** Basic auth (what the SDK sends, `api:<key>`), Bearer, or
  `x-api-key`.
- **Uploads:** they use signed URLs instead of keys, signed with an HMAC secret stored in the
  `meta` table.
- **No authorization:** any valid key can write to any entity and read everything.
  [Section 7](#7-known-gaps-candidates-for-002) lists the consequences.

### D9. Deployment: a plain FastAPI app

- **Layout:** `app.py` at the tracker root builds `app` from `TRACKER_*` environment
  variables, and is served with `fastapi run app.py` (`fastapi dev` in development). There
  is no `__main__.py` and no package `__init__.py` at the tracker root, and the code
  imports itself as `src.…`.
- **Migrations live only in `src/db/migrations/NNNN_name.sql`.** The server applies pending
  ones on startup under an advisory lock, each in its own transaction, and records them in
  `schema_migrations`. With `TRACKER_MIGRATE_ON_STARTUP=false` it refuses to start on a
  pending schema instead.
- **Lockfile:** the Docker image installs the tracker's `pyproject.toml` at the versions
  pinned in the root `uv.lock`, with hashes. The lockfile is outside the build directory, so
  it arrives through a BuildKit named context (`additional_contexts: workspace: ../..` in
  compose, `--build-context workspace=../..` with plain Docker). A bare `docker build .`
  can't work, and that's accepted.
- **Shutdown:** uvicorn waits for open responses before it runs lifespan shutdown, and SSE
  responses never end. The app therefore chains SIGINT and SIGTERM to close every live
  stream first, which took `docker stop` from 15.3 s to 0.4 s.
- **Access logs:** they're disabled inside the lifespan, because fastapi-cli configures
  logging after it imports the app.

### D10. Code conventions (the user's rules)

- **Layout:** `src/api/` is the HTTP layer, `src/core/` the in-memory engine, `src/db/`
  PostgreSQL access and migrations, `src/scripts/` dev scripts.
- **Where helpers go:**
  - a helper used once or twice is written inline, with a one-line comment;
  - a helper used repeatedly within a class becomes a method or staticmethod of that class;
  - a helper used across modules goes in `src/utils.py`.
- **Defaults** live as class or function defaults (`ClassVar`s, dataclass fields), not as
  module-level constants.
- **Settings:** each setting is a dataclass field with `field(default=..., doc=...)`.
  Environment values are coerced by the default's type, with bools parsed from words: plain
  `bool("false")` is `True`.
- **No per-file docstrings.** Comments explain why, not what.

---

## 3. wandb protocol facts

These were learned by capturing real SDK 0.31 traffic. Re-check them when the SDK is
upgraded.

- **Login:** `wandb login --host ...` requires a 40-character key and calls `Viewer`. An
  unknown key must produce 401 so that login fails.
- **Init:** `UpsertBucket` creates or updates a run. Its variables include `config` and
  `summaryMetrics` as JSON strings, which may contain `NaN`. A resumed run may arrive with
  only an `id`, which is `base64("Run:v1:<name>:<project>:<entity>")`.
- **Resume:** `resume="must"` calls `RunResumeStatus` and must get `null` for an unknown
  run. `allow` continues from the stored line counts and summary.
- **`file_stream`** is a gzip JSON POST to `/files/{entity}/{project}/{run}/file_stream`
  containing `files: {name: {offset, content: [lines]}}`:
  - `wandb-history.jsonl` lines are the rows logged with `run.log`.
  - `wandb-summary.json` is a full replacement each time: use the last element of `content`.
  - `wandb-events.jsonl` lines are system metric samples.
  - `output.log` lines are console output.
  - The final request has `complete: true` and `exitcode`. A preempted run sends
    `preempting: true`.
- **Heartbeats:** every `file_stream` request counts as a heartbeat. A run with no request
  for `crash_timeout` seconds is marked `crashed`.
- **Flush timing:** the SDK can hold its first flush for a while (~15 s). Tests that need
  data mid-run set `x_file_stream_transmit_interval` and `x_stats_sampling_interval`, or
  wait for the run's state rather than sleeping.
- **Run files:** `CreateRunFiles` returns upload URLs, which the server signs. The SDK PUTs
  each file there and then calls `MarkRunFilesUploaded`. Validate file names, because
  they're used as paths.
- **Telemetry:** wandb-core exports its own OpenTelemetry data to `/sdk/otel/...`. Accept
  it and discard it.
- **Error shape:** GraphQL errors must use `{"errors": [{"message": ...}]}` so that the SDK
  surfaces the message.

---

## 4. Plan

Each step lists what to build, when it's done, and the traps that were hit along the way.
The tests named in the "Done when" lines exist in `tests/tracker/`.

### Step 0. Workspace and protocol capture

- **Build:**
  - Add `flock/tracker/pyproject.toml` (asyncpg, fastapi, fastapi-cli, numpy, orjson,
    uvicorn, uvloop, httptools; `[tool.uv] package = false`) as a member of the uv
    workspace.
  - In the root `pyproject.toml`, add `pythonpath = ["flock/tracker"]` for pytest,
    `extra-paths` for ty, and `known-first-party = ["src", "app"]` for ruff. Add `wandb` to
    the test dependencies.
- **Capture:** run the real SDK against a stub that logs every request and answers
  minimally, to learn the operations in [section 3](#3-wandb-protocol-facts).
- **Pitfall:** `uv add` and `uv remove` prune extra packages from a shared venv. Use
  `--no-sync`, then `uv sync --all-packages --inexact`.

### Step 1. Schema and migration runner

- **Build:**
  - `src/db/migrations/0001_initial.sql`: the tables `runs`, `history`, `events`, `logs`,
    `history_columns` (with the TOAST settings from D5), `run_files`, and `meta` with
    `upload_secret`.
  - `src/db/migrate.py` (`Migrations`).
  - `src/db/database.py`, which takes the server lock, migrates if allowed, refuses while
    migrations are pending, and opens an asyncpg pool.
- **Done when:** `test_migrations.py` passes: contiguous numbering, migrations apply once,
  the server migrates on startup or refuses when told not to, and only one server runs per
  database.

### Step 2. Settings, app skeleton and test harness

- **Build:**
  - `src/settings.py`.
  - `app.py` with `create_app(store)`: the lifespan opens the store, then `gc.freeze()`,
    then starts the crash watcher. Add error handlers for `APIError` and `FilterError`,
    optional CORS, and `/healthz`.
- **Build the test harness now**, not at the end (`tests/tracker/conftest.py`):
  - one throwaway `postgres:18.4` container on tmpfs with fsync off, or
    `TRACKER_TEST_DATABASE_URL`;
  - migrations applied once to a template database, which each test server copies;
  - an in-process `TrackerServer`;
  - an HTTP `Client`;
  - a `WandbClient` that runs the real SDK in a subprocess.
- **Pitfall:** pytest needs `--import-mode=importlib`, because `src` and `tests` aren't
  installed packages.

### Step 3. Write path: GraphQL, file_stream and uploads

- **Build:**
  - `src/api/wandb_protocol.py`: the `GraphQL` dispatcher from D1, `file_stream`
    (decompress gzip; return 400 for bad JSON or corrupt gzip), and `PUT /storage/...` with
    signature checks.
  - `src/api/deps.py`: the orjson `JSONResponse` with numpy support, `APIError`, and the
    `StoreDep`, `UserDep`, `ProjectDep` and `RunDep` dependencies.
  - In `src/core/store.py`: `upsert_run` and `file_stream`, using the single CTE from D3,
    with per-run asyncio locks.
- **Done when:** `test_wandb_integration.py` passes. It covers login (accepted and
  rejected), init/log/summary/config, failed exit codes, resume `allow` and `must`, restart
  survival, offline + `wandb sync`, concurrent runs, keys mapping to entities, and a killed
  client being marked crashed.

### Step 4. Run table and filters

- **Build:** `src/core/table.py` (`ColumnTable`, `Column`, the filter compiler and
  `FilterError`). Keep it in sync on every upsert, summary and state change.
- **Startup:** load every run with a server-side cursor and fill columns with `bulk_set`.
  Setting cells one at a time is too slow for 15M cells.
- **Done when:** the filter and sort tests in `test_query_api.py` pass.

### Step 5. Series engine and compaction

- **Build:** `src/core/history.py`:
  - `Series`, with `encode` and `decode` and dense elision;
  - `PointCounter`;
  - `History`, which tracks which keys are loaded (`get` raises `LookupError` for an
    unloaded key) and provides `append_rows`, `curve(key, x_key, max_points)` and
    `delta(from_row, x_key)`.

  Then the store's LRU, `histories()` with batched cold loads and single-flight rebuilds, and
  background compaction when a run turns inactive. At startup, rebuild any stream with
  `compacted < lines` (crashed runs), one run at a time so memory stays bounded.
- **Done when:** `test_history.py` passes, along with the compaction and eviction tests in
  `test_query_api.py`.
- **Pitfalls:**
  - Evict after compaction as well as after inserts, or the cache never shrinks.
  - Interpolate the stream name into SQL only after validating it against `STREAMS`.

### Step 6. Read API

- **Build:** `src/api/runs.py`. Pydantic query models are shared by GET (query string) and
  POST (JSON body). Validators split comma-separated lists and parse filters; raise
  `APIError` from validators so the error isn't wrapped as a 422. The endpoints are listed
  in the README.
- **Done when:** `test_query_api.py` passes.

### Step 7. Dashboard snapshot and live stream

- **Build:** `src/api/dashboard.py` (`Snapshot`, `FLK2`, `/workspace`, `/stream`) and
  `src/core/live.py` (`Hub` and `Subscriber`). Push point deltas from `file_stream` only to
  subscribers watching that run.
- **Done when:** `test_workspace.py` passes. It covers the binary layout, the point budget,
  replay from cursors, live pushes, created and run events, and disconnecting slow
  subscribers.

### Step 8. Latency suite

- **Build:** `test_latency.py`:
  - **Curves:** 50+ runs × 500 points, measured warm, cold (cache dropped), with several
    metrics, and for ongoing runs while they log.
  - **Filters:** a 10k-run × 1.5k-column sweep running single-metric, compound+sort,
    `$or`+regex+strings, all-ids, wide-projection and column-search queries.

  Assert p50 and p95 against the budgets, measured end to end over keep-alive HTTP,
  including decoding.
- **Done when:** every budget passes with headroom. If cold curves are slow, check the
  TOAST settings and dense elision in D5 first.

### Step 9. System metrics

- **Build:**
  - `0002_system_metrics.sql` adds `history_columns.stream` (part of the primary key) and
    renames `compacted_line_count` to `history_compacted_line_count`.
  - It also adds `system_compacted_line_count`.
  - Key every cache and compaction structure by `(run_pk, stream)`.
  - Expose the stream as described in D6.
- **Upgrading existing data:** system lines already in `events` predate the migration, so
  they show up as `compacted < lines` and are rebuilt at startup.
- **Done when:** `test_system_metrics.py` passes, along with the 0002 upgrade test in
  `test_migrations.py`.

### Step 10. Packaging

- **Build:** a two-stage `Dockerfile`:
  - Stage 1 runs `uv export --frozen --package flock-tracker --no-dev --no-emit-workspace`
    and installs the result with `uv pip install --require-hashes`, then copies `app.py`
    and `src/`.
  - Stage 2 is `python:3.14-slim` with a non-root user, `/healthz` as the healthcheck, and
    `CMD fastapi run /app/app.py`.

  Also add `compose.yaml` (tuned PostgreSQL plus the tracker, which waits for PostgreSQL to
  be healthy, uses named volumes and has a 15 s stop grace period) and `.dockerignore`. Add
  the signal chaining and access-log silencing from D9.
- **Done when:** on a fresh volume, `docker compose up -d --build` applies 0001 and 0002. A
  real wandb run, including its system metrics, is then readable, and it survives an image
  rebuild. `docker stop` with an open SSE stream takes under 1 s.

### Step 11. Docs and demo

- **Build:** the README (running it, pointing clients at it, code layout, migrations,
  supported features, read API, snapshot format, design, performance, tests). Also
  `src/scripts/demo.py`, which seeds realistic projects and streams live runs for UI work
  (`uv run python -m src.scripts.demo`).

---

## 5. How it was actually built

The actual order was: SQLite PoC on a hand-rolled server, then a migration to FastAPI, then
PostgreSQL with a migrations folder and Docker, then a folder reorganization under the D10
rules, then self-contained compose, then system metrics, then the flat
`app.py` + `src/` layout. Lessons from those detours:

- **Shape the deployment early.** The FastAPI, PostgreSQL and layout changes each cost a
  pass over every module and test. Start from D9.
- **Write the latency suite before optimizing.** Every real win came from measuring: TOAST
  settings, dense elision, batched cold loads and the O(1) point counter.
- **Test with the real SDK.** Each of these came from an integration test with real wandb,
  not from reading its source:
  - the duplicate-append-on-resume bug;
  - crash-test flakiness from delayed flushes;
  - the shutdown hang.
- **Watch the server's own lifecycle once a framework owns it.** fastapi-cli re-enabled
  access logs and blocked shutdown on SSE streams.

---

## 6. Environment notes for agents

- Run the suite from the repository root: `uv run python -m pytest tests/tracker`. It needs
  Docker, or `TRACKER_TEST_DATABASE_URL`. Check with ruff and ty.
- Never run `git stash pop`. The user keeps an unrelated stash.
- After any dependency change, run `uv sync --all-packages --inexact` so that other
  packages in the shared venv survive.
- zsh treats unquoted `*` and `$VAR` differently from bash. Quote them, or write a small
  Python script.
- Don't pipe long-running commands into `head`, because SIGPIPE kills them. Redirect to a
  log file instead.

---

## 7. Known gaps (candidates for 002+)

- **Auth (D8):**
  - The server is open when `TRACKER_API_KEYS` is empty, which is the compose default.
  - Keys aren't scoped: any valid key can write to any entity and read all of them.
  - Keys are plain text in the environment, and there's no way to rotate them.
  - Keys are compared with a dict lookup, not a timing-safe comparison.
- **Live stream auth in browsers:** a browser `EventSource` can't send headers, so the live
  stream can't authenticate once keys are on. It needs a query or cookie credential, or a
  fetch-based SSE reader.
- **Projects (D8):** they have no records of their own, so they can't be created, renamed,
  deleted or described. `GET /projects` lists every project to every key.
- **Lifecycle:** there's no way to delete or archive runs and no retention policy, so
  memory and startup time grow without bound.
- **Scale-out:** the design allows exactly one replica (D2). Going beyond that needs a
  shared cache-invalidation story, or a read replica with its own load.
- **Unsupported SDK features:** artifacts, sweeps, and any GraphQL operation not in
  `GraphQL.OPERATIONS` or `GraphQL.STATIC`.
