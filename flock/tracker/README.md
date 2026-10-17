# Tracker

The tracker is a wandb-compatible experiment tracking server backed by PostgreSQL. Training
code logs with the regular `wandb` SDK (`wandb.init`, `run.log`, `run.summary`, `run.config`).
Dashboards read loss curves and filtered run tables from a low-latency API.

## Running it

### With Docker Compose

[`compose.yaml`](compose.yaml) runs PostgreSQL and the server, which applies any pending
migrations when it starts. From this directory:

```sh
docker compose up -d --build
```

The server listens on port 8080. Data lives in two named volumes: `postgres-data` for the
database and `tracker-files` for uploaded run files. Configure it through environment
variables or an `.env` file next to `compose.yaml`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `tracker` | database password; **set this outside local testing** |
| `TRACKER_PORT` | `8080` | host port |
| `TRACKER_API_KEYS` | (empty) | `user:key,user:key`; empty accepts any key as `TRACKER_DEFAULT_ENTITY` |
| `TRACKER_DEFAULT_ENTITY` | `flock` | entity runs land in when API keys aren't configured |
| `TRACKER_PUBLIC_URL` | request URL | external base URL, for upload URLs behind a proxy |
| `TRACKER_CRASH_TIMEOUT` | `600` | seconds without heartbeats before a run is marked `crashed` |
| `TRACKER_HISTORY_CACHE_POINTS` | `20000000` | history points cached in memory for finished runs |
| `POSTGRES_SHARED_BUFFERS` | `1GB` | PostgreSQL `shared_buffers` |

Run exactly one tracker per database. The server keeps its working set in memory and holds a
database lock, so a second instance refuses to start.

The image installs this directory's `pyproject.toml` dependencies at the versions locked in the
workspace's `uv.lock`. That lockfile lives at the repository root, outside this directory, so
the build takes the repository root as an extra `workspace` context. Compose passes it
automatically; with plain Docker, pass it yourself:

```sh
docker build --build-context workspace=../.. -t flock-tracker .
```

### Locally

It's a standard FastAPI app, so the FastAPI CLI serves it. From this directory:

```sh
TRACKER_DATABASE_URL=postgresql://user:pass@host/db uv run fastapi run app.py --port 8080
```

Use `fastapi dev app.py` instead to reload on code changes. Configure the server with
`TRACKER_<SETTING>` environment variables, for example `TRACKER_DATABASE_URL` or
`TRACKER_FILES_DIR`. [`Settings`](src/settings.py) lists every setting along with its default
and description.

### Pointing clients at it

```sh
wandb login --host http://tracker.internal:8080 <any 40 character key>
# or, per process:
export WANDB_BASE_URL=http://tracker.internal:8080 WANDB_API_KEY=<key>
```

## Code layout

| Path | Contents |
| --- | --- |
| `app.py` | the FastAPI app: lifecycle, error handling and routers |
| `Dockerfile`, `compose.yaml` | the image and the Compose stack |
| `src/api/` | HTTP layer: the wandb protocol, projects and runs, dashboard snapshots and streams |
| `src/core/` | in-memory engine: the run store, columnar run table, series and live events |
| `src/db/` | PostgreSQL connection, migration runner and the SQL migrations |
| `src/settings.py`, `src/utils.py` | configuration, and the few helpers shared across modules |
| `docs/` | further documentation |

## Migrations

The schema is defined only by the SQL files in [`src/db/migrations/`](src/db/migrations/).
Their names are `NNNN_description.sql` and they apply in order. When the server starts, it
applies the ones that are pending, each in its own transaction, and records them in
`schema_migrations`. An advisory lock makes concurrent starts safe. With
`TRACKER_MIGRATE_ON_STARTUP=false` the server applies nothing and refuses to start while
migrations are pending.

To change the schema, add the next numbered file. Never edit a migration that has already
been applied. `tests/tracker/test_migrations.py` checks that the numbering stays contiguous.

## What is supported

The server supports logging, viewing and filtering runs, plus managing projects and runs
through its own API (see [Managing projects and runs](#managing-projects-and-runs)). It doesn't
implement artifacts, sweeps, reports or wandb's own web UI.

| wandb feature | Support |
| --- | --- |
| `wandb login` / `wandb.init` (name, config, tags, notes, group, job_type, `id`) | yes |
| `run.log` (incl. nested dicts, explicit `step`, NaN/inf, media values) | yes |
| `run.summary`, `define_metric(summary=...)` | yes |
| `run.config.update`, `run.tags`, `run.notes` | yes |
| `resume="allow" \| "must"` | yes |
| exit codes (`finished` / `failed`), preemption, crash detection | yes |
| system metrics (CPU, GPU, memory, disk, network samples from the SDK's monitor) | yes |
| console output, run files (config.yaml, requirements.txt, wandb-metadata.json, ...) | yes |
| offline runs + `wandb sync` | yes |

## Read API

Interactive docs are served at `/docs`. Every path below is relative to
`/api/v1/projects/{entity}/{project}`. Endpoints that select runs accept query parameters via
GET, or the same fields as a JSON body via POST to `.../query`.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/projects[?entity=]` | projects with run counts, including empty ones |
| `GET /api/v1/projects/{entity}/{project}` | one project |
| `GET .../columns?q=&prefix=` | run-table columns, their type and coverage |
| `GET .../runs`, `POST .../runs/query` | filter, sort and page the runs table |
| `GET .../history`, `POST .../history/query` | curves for many runs (default: newest 50; `running=true` for ongoing ones) |
| `GET .../runs/{run}` | run attributes, config, summary, history and system metric names |
| `GET .../runs/{run}/history` | curves for one run |
| `GET .../runs/{run}/logs` | console output |
| `GET .../runs/{run}/files[/{name}]` | uploaded run files |
| `GET .../workspace` | binary snapshot of a dashboard page (see below) |
| `GET .../stream?runs=id:lines[:system_lines],...` | Server-Sent Events with live updates (see below) |

Run-table columns are the run attributes (`name`, `display_name`, `state`, `created_at`,
`tags`, `group`, `job_type`, ...) plus flattened `config.*` and `summary.*` keys
(`summary.val_loss.min`, `config.model.layers`). Filters use the Mongo-style syntax that
`wandb.Api().runs(filters=...)` uses:

```json
POST /api/v1/projects/flock/llm/runs/query
{
  "filter": {"$and": [{"config.optimizer": {"$in": ["adam", "adamw"]}},
                      {"summary.loss": {"$lt": 2.5}},
                      {"tags": "baseline"}]},
  "sort": "-summary.loss",
  "columns": ["display_name", "state", "summary.loss", "config.*"],
  "limit": 100
}
```

The supported operators are `$eq $ne $gt $gte $lt $lte $in $nin $exists $regex $contains`,
combined with `$and $or $nor $not`. On list columns such as `tags`, `$eq` and `$in` test
membership. A missing value never satisfies a comparison, and it does satisfy `$ne`. The
response is column-oriented: it has `ids` and `data[column]` lists of the same length, and
`all_ids: true` adds the ids of every match.

`history` returns `runs[i].series[key] = {"x": [...], "y": [...]}`. You choose the x-axis with
`x` (`_step` by default, or `_runtime` or any logged metric such as `epoch`), and you can cap
each curve's size with `max_points`. Non-finite values come back as `null`.

## Managing projects and runs

Runs are created only by the wandb SDK, and their config, summary, history and logs only change
through it. The API manages everything else. Paths are relative to `/api/v1/projects`. There is
no authorization beyond the API key check yet, so any accepted key can rename and delete.

| Endpoint | Effect |
| --- | --- |
| `POST /api/v1/projects` `{entity, name, description?}` | create an empty project (`wandb.init` also creates projects); 409 if it exists |
| `PATCH /{entity}/{project}` `{name?, description?}` | rename it or change its description |
| `DELETE /{entity}/{project}[?force=true]` | delete it with all its runs, history, logs and files |
| `PATCH .../runs/{run}` `{display_name, notes, tags, group, job_type, description}` | edit a run; only the fields given change, and `null` clears one |
| `DELETE .../runs/{run}[?force=true]` | delete a run with everything it logged |
| `POST .../runs/update` `{selection, set?, add_tags?, remove_tags?}` | edit many runs |
| `POST .../runs/delete` `{selection, force?}` | delete many runs |
| `POST .../runs/move` `{selection, to, create?}` | move runs to another project of the same entity |

A *selection* names `runs` (ids), or gives a `filter` (and/or `running: true`), or sets
`all: true`. A body that selects nothing is rejected rather than taken to mean every run, and
`dry_run: true` returns the ids that would change without changing them.

Deletes are hard: there is no undo. Project names given through the API must be 1–128
characters without `/ \ # ? % :`. Operations that conflict answer 409 with the `ids` in the
way, and change nothing:
- Renaming a project, or moving a run, while runs are active. Their clients address them by
  the old path in every request.
- Deleting active runs, or a project with active runs, without `force=true`. After a forced
  delete, the run's client finishes normally, but what it still sends is dropped, so the run
  stays deleted. Its id becomes free again once the client has been quiet for
  `TRACKER_CRASH_TIMEOUT`.
- Moving a run into a project that already has a run with its id.

A moved run keeps everything it logged and sorts by its creation time in its new project. A
renamed project's old name is free again: a client resuming at the old path starts a new run
there. Edits race with live clients on equal terms: if the SDK later sends tags or notes, they
replace the edit.

### System metrics

The wandb SDK samples the machine every 15 s by default (`x_stats_sampling_interval`), in a
separate stream from what `run.log` records. That stream holds CPU, memory, disk, network and
GPU readings, with keys like `system.cpu`, `system.memory_percent` and `system.gpu.0.gpu`. The
samples carry `_runtime` and `_timestamp` but no `_step`, so they are plotted against time.

Pass `stream=system` to either history endpoint to read them; `x` then defaults to `_runtime`.
For example, `GET .../history?stream=system&keys=system.gpu.0.gpu&running=true` returns the GPU
utilization of every ongoing run. `GET .../runs/{run}` lists a run's `system_keys` and
`system_line_count`. The snapshot and the live stream include system metrics when asked with
`system=true` (see below).

### Dashboard snapshot and live stream

`GET .../workspace` returns everything a dashboard page shows in one response. That is a page
of the runs table with every column, plus every metric's curve for each run on the page,
downsampled to fit a point budget. It is binary, so a page of 100 runs × 1.5k columns × dozens
of metrics stays a few MB and decodes into typed arrays without parsing. With `system=true`
it also carries every system metric's curve. The layout is little-endian:

```text
b"FLK2"  u32 header length  header JSON  (zero-padded to 8 bytes)
f64[numeric columns × runs]        numeric table cells, column-major (NaN = missing)
f64[runs × series × 6]             per series: y offset, n, raw count N, x0, dx, x offset
f64[...]                           x values of irregular series
f32[...]                           y values
```

Each run's series are the header's `metrics` against `_step`, followed by its
`system_metrics` against `_runtime`. `system_metrics` is empty unless `system=true` was asked
for, so the layout is unchanged for clients that don't ask. Series are sampled at the raw
indices `k_i = (i·(N-1) + (n-1)//2) // (n-1)`. When a series' x values are evenly spaced, which
is the usual case for `_step`, they are `x0 + dx·k_i` and aren't sent, so the x offset is -1.
Otherwise they are stored explicitly at the x offset. Header `runs` carry each run's history
`lines` and `system_lines` counts.

`GET .../stream` sends Server-Sent Events about the runs on a page:
- `hello` carries the current state of every watched run.
- `history` carries new points.
- `system` carries new system metric samples, against `_runtime`. These are only sent when
  the stream was opened with `system=true`.
- `run` carries state changes, the line count, changed `summary.*` columns and, after an
  edit, the changed `attributes`.
- `created` announces a new run in the project, or one moved in.
- `deleted` lists runs that were deleted, or moved out (with `moved_to`).
- `project` says the project was renamed (`renamed_to`) or `deleted`; the stream then ends.

The snapshot records each run's line counts. Pass them back as `runs=<id>:<lines>,...`, or
`runs=<id>:<lines>:<system_lines>,...` with `system=true`. The stream first replays every
point logged since the snapshot, so a dashboard never misses data between rendering and
subscribing. Each subscriber has a
bounded queue. A subscriber that falls behind is disconnected rather than buffered without
limit; it reconnects with its latest line counts.

## Design

- **Storage**: PostgreSQL holds everything durably. Raw wandb JSON (config, summary, history
  and event lines) is stored as `text` rather than `jsonb`, because wandb writes `NaN` and
  `Infinity` literals, which `jsonb` rejects. Each `file_stream` request is applied with a
  single SQL statement, so it costs one round trip and is atomic. That statement uses
  data-modifying CTEs to upsert history, event and console lines and update the run. A
  request is acknowledged only after it commits, and re-sent chunks are idempotent because
  lines are keyed by `(run, line number)`.
- **Run table**: each project keeps every run's attributes, config and summary in memory as
  columns. Each column has a float64 array, an object array for non-numeric values and a
  presence mask, so a filter compiles to vectorized numpy operations over all runs at once
  and never touches the database.
- **History**: a run has two series streams, its logged history and its system metrics. Both
  are handled the same way. Running runs and recently read runs are kept in memory as
  columnar `(row, value)` arrays per metric, in an LRU bounded by the history cache size. When
  a run finishes, each stream is compacted into one `bytea` blob per metric. These blobs are stored
  uncompressed and out of line, so reads skip decompression. Curves of cold runs load in a
  single batched query per request (`run_pk = ANY(...) AND key = ANY(...)`), whatever the
  number of runs.
- **Changes**: deleting or moving runs rebuilds the affected projects' run tables, keeping rows
  in creation order (tens of ms for 10k runs × 1.5k columns). Mutations take a lock per
  project and per run, always in the same order, and writes from a run's client hold its
  lock, so a run is never deleted halfway through a write.
- **Startup** loads every run's metadata, config and summary into memory. A 10k-run ×
  1.5k-column project takes a few seconds. Objects loaded at startup are frozen out of the
  cyclic GC so its collections don't pause requests.
- Responses are encoded with orjson, which serializes numpy arrays directly.

## Performance

`tests/tracker/test_latency.py` measures each request end to end. That includes HTTP over a
keep-alive connection and decoding the response on the client. These are the numbers on an
Apple Silicon laptop, with PostgreSQL running in Docker:

| Scenario | p50 | Budget |
| --- | --- | --- |
| `loss` curves, newest 50 runs × 500 points (5k steps, logged every 10) | ~2 ms | 10–20 ms |
| same, cold (all loaded from PostgreSQL) | ~4 ms | 10–20 ms |
| `loss,val_loss,lr` for the newest 50 runs | ~4.5 ms | 10–20 ms |
| ongoing runs' curves while those runs stream history | ~1 ms | 10–20 ms |
| 6-clause filter + sort over 10k runs × 1.5k columns | ~2.5 ms | 20–50 ms |
| `$or` + regex + string `$in` over 10k runs × 1.5k columns | ~3 ms | 20–50 ms |
| deleting ~950 runs from 10k runs × 1.5k columns | ~90 ms | 200 ms |
| moving 500 of those runs to another project | ~50 ms | 200 ms |

## Tests

The tests need PostgreSQL. They use the admin URL in `TRACKER_TEST_DATABASE_URL` if it is set.
Otherwise they start a throwaway `postgres:18.4` container, which requires Docker. Migrations
are applied once to a template database, and each test server gets a fresh copy of it.

```sh
uv run python -m pytest tests/tracker
```
