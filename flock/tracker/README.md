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
| `TRACKER_DEFAULT_ENTITY` | `flock` | organization created for the first user when there is none |
| `TRACKER_PUBLIC_URL` | request URL | external base URL, for upload URLs and the Google callback behind a proxy |
| `TRACKER_APP_URL` | `http://localhost:5173` | dashboard (flock-ui) base URL that invite, reset and sign-in links point at |
| `TRACKER_JWT_SECRET` | generated | key signing access tokens; by default one is generated into the database |
| `TRACKER_GOOGLE_CLIENT_ID`, `TRACKER_GOOGLE_CLIENT_SECRET` | (empty) | enable Sign in with Google |
| `TRACKER_GOOGLE_REDIRECT_URL` | `<app URL>/api/v1/auth/google/callback` | the callback registered with Google |
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

Every request needs credentials (see [Accounts and organizations](#accounts-and-organizations)).
The SDK uses a personal API key, created with `POST /api/v1/me/api-keys`:

```sh
wandb login --host http://tracker.internal:8080 <your API key>
# or, per process:
export WANDB_BASE_URL=http://tracker.internal:8080 WANDB_API_KEY=<your API key>
```

`wandb.init` logs to your default organization unless you pass `entity=`.

## Code layout

| Path | Contents |
| --- | --- |
| `app.py` | the FastAPI app: lifecycle, error handling and routers |
| `Dockerfile`, `compose.yaml` | the image and the Compose stack |
| `src/api/` | HTTP layer: the wandb protocol, auth, organizations, projects and runs, dashboard snapshots and streams, the home feed and budgets |
| `src/core/` | in-memory engine: the run store, accounts, columnar run table, series, live events, GPU spend and the home feed |
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

## Accounts and organizations

An **organization** is a wandb entity: `wandb.init(entity="acme")` logs into the org `acme`,
and every project belongs to one. Users belong to any number of orgs, with a role in each.

| Who | Can |
| --- | --- |
| super admin | create organizations; manage users; act as an admin of every org |
| org admin | invite people; change roles and remove members; rename or delete the org; update or delete projects; edit, move or delete any run |
| member | read everything in the org; create projects (with the API or `wandb.init`); log runs; edit, move or delete the runs they logged |

Orgs you don't belong to answer 404, as if they didn't exist. Changing runs you didn't log
answers 403 with their `ids`. An org always keeps an admin, and the server a super admin.

**Getting started.** The first account to sign up becomes the super admin, and an admin of
every org (an org named `TRACKER_DEFAULT_ENTITY` is created if there are none). After that,
signing up is closed, and people join through invitations. Until that account exists, every
request is refused; the server logs where to create it, and flock-ui opens on a setup page
that does. The same with the API:

```sh
curl -X POST $TRACKER/api/v1/auth/signup -d '{"email": "me@lab.org", "name": "Me", "password": "..."}'
# -> {"access_token", "refresh_token", "expires_in", "user"}
curl -X POST $TRACKER/api/v1/orgs/flock/invites -H "authorization: Bearer $TOKEN" \
     -d '{"email": "colleague@lab.org", "role": "member"}'
# -> {"url": "<TRACKER_APP_URL>/invite/<token>", ...}: send them the link yourself
```

The tracker sends no email and verifies no addresses: the link is the proof. Its page accepts
the invitation with `POST /api/v1/invites/{token}/accept` and `{name, password}`, or, for
someone who already has an account, signed in as the invited email. Password resets work the
same way. A super admin (or an admin of an org the user belongs to, unless the user is a super
admin) creates a one-time link with `POST /api/v1/users/{id}/password-reset`.

**Credentials.** The dashboard signs in with `POST /api/v1/auth/login` and sends
`Authorization: Bearer <access_token>`. Access tokens are JWTs valid for 15 minutes; refresh
them with `POST /api/v1/auth/refresh` (each refresh token works once). Logging out, changing
your password or a password reset end sessions at once, not when their tokens expire. The wandb
SDK and scripts use API keys, sent as the SDK does (basic auth), as `Bearer <key>` or as
`x-api-key`. Keys are stored hashed and shown only when created.

**Sign in with Google** runs the OAuth code flow in the tracker. Register
`<TRACKER_APP_URL>/api/v1/auth/google/callback` with Google: flock-ui proxies
`/api/v1/auth/google/*` to the tracker, so the tracker needn't be reachable from browsers (set
`TRACKER_GOOGLE_REDIRECT_URL` if your dashboard doesn't). Then send the browser to
`/api/v1/auth/google/start[?invite=<token>]`. It comes back to
`<TRACKER_APP_URL>/auth/google?code=...`, and the dashboard exchanges the code with
`POST /api/v1/auth/google/exchange`; failures land on `<TRACKER_APP_URL>/login?error=...`.
Google accounts are matched to existing accounts by verified email.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/auth/config` | whether signup is open and Google is enabled (public) |
| `POST /api/v1/auth/signup`, `login`, `refresh`, `logout` | sessions |
| `GET`/`PATCH /api/v1/me`, `POST /api/v1/me/password` | your profile (`name`, `default_org`) and password |
| `GET`/`POST /api/v1/me/api-keys`, `DELETE .../{id}` | your API keys |
| `GET /api/v1/orgs`, `POST /api/v1/orgs` | your orgs; create one (super admins) |
| `GET`/`PATCH`/`DELETE /api/v1/orgs/{org}` | an org; rename or delete it (admins; `?force=true` deletes active runs too) |
| `GET /api/v1/orgs/{org}/members`, `PATCH`/`DELETE .../{user_id}` | members; change roles or remove them (admins), or leave |
| `GET`/`POST /api/v1/orgs/{org}/invites`, `DELETE .../{id}` | pending invitations (admins) |
| `GET /api/v1/invites/{token}`, `POST .../accept` | inspect and accept an invitation (public) |
| `GET /api/v1/users`, `PATCH`/`DELETE .../{id}` | all users; grant super admin; delete accounts (super admins) |
| `POST /api/v1/users/{id}/password-reset`; `GET`/`POST /api/v1/password-resets/{token}` | password reset links |

Renaming an org renames the entity of its projects and runs, so like a project rename it's
refused while runs are active. Deleting one deletes all its projects and runs.

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
`tags`, `group`, `job_type`, `owner`: the id of the user who logged it, `gpu_type`, `gpu_count`,
`gpu_hours`, ...) plus flattened
`config.*` and `summary.*` keys (`summary.val_loss.min`, `config.model.layers`). Filters use
the Mongo-style syntax that `wandb.Api().runs(filters=...)` uses:

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
through it. The API manages everything else. Paths are relative to `/api/v1/projects`. Members
create projects and change their own runs; updating or deleting projects, and changing other
people's runs, takes an org admin.

| Endpoint | Effect |
| --- | --- |
| `POST /api/v1/projects` `{entity, name, description?}` | create an empty project (`wandb.init` also creates projects); 409 if it exists |
| `PATCH /{entity}/{project}` `{name?, description?}` | rename it or change its description |
| `DELETE /{entity}/{project}[?force=true]` | delete it with all its runs, history, logs and files |
| `PATCH .../runs/{run}` `{display_name, notes, tags, group, job_type, description, gpu_type, gpu_count}` | edit a run; only the fields given change, and `null` clears one (or, for the GPU fields, goes back to the detected value) |
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

### GPU spend, budgets and the home feed

The tracker reads each run's GPU type and count from the `wandb-metadata.json` the SDK uploads
(`NVIDIA H100 80GB HBM3` × 8 becomes `gpu_type: H100`, `gpu_count: 8`); owners and admins
correct them with `PATCH .../runs/{run}` (multi-node runs report one node, and NVML counts
GPUs hidden by `CUDA_VISIBLE_DEVICES`). Each heartbeat adds the time since the previous one to
a ledger of seconds per run and UTC month, unless the run was silent longer than
`TRACKER_CRASH_TIMEOUT` (preemption gaps aren't billed). Ledger rows outlive their runs and
projects, so deleting never gives budget back. Org admins set prices per GPU hour and a
monthly budget; cost is GPU hours × the current price.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/feed?org=&project=&owner=me&kinds=&cursor=&limit=` | home feed, newest first: `run_started`, `run_finished`/`run_failed`/`run_crashed`, `project_created` |
| `GET /api/v1/orgs/{org}/billing` | currency, budgets, prices, and every GPU type the org's runs used |
| `PATCH /api/v1/orgs/{org}/billing` `{monthly_budget?, currency?, rates?: {"H200": 60, "A100": null}}` | admins: set the budget from this month on, and merge prices (`null` removes one) |
| `GET /api/v1/orgs/{org}/budget[?month=YYYY-MM]` | spent, remaining, cost per project (`null`: deleted projects), top 5 runs, unpriced GPU hours |

```bash
curl -s -X PATCH --json '{"monthly_budget": 20000, "rates": {"H200": 60}}' \
  -H "Authorization: Bearer $TOKEN" $TRACKER/api/v1/orgs/flock/billing
curl -s -H "Authorization: Bearer $TOKEN" $TRACKER/api/v1/orgs/flock/budget
```

The feed is derived from runs and projects as they are (no activity log), so it has no
deletes, renames or membership changes, and isn't pushed live. See
[`docs/agents/004-home-and-budgets.md`](docs/agents/004-home-and-budgets.md).

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
- `project` says the project was renamed (`renamed_to`), its organization was renamed
  (`entity_renamed_to`), or it was `deleted`; the stream then ends.

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
- **Auth**: users, memberships, API key digests and sessions are loaded into memory, so
  authenticating a request costs a sha256 (API key) or an HMAC (JWT) and a dict lookup, never a
  database round trip. Roles aren't put in tokens: they're looked up per request, so changes
  apply at once. Passwords are hashed with argon2id in a worker thread.
- **Spend**: the usage ledger (`gpu_usage`), prices and budgets are in memory too. Accruing
  is one more CTE in the `file_stream` statement; the budget panel reads only the month's
  ledger entries of one org, and the feed takes the latest rows of each project's run table
  with `argpartition`.
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
| home feed, first page and a filtered later page, over 10k runs | ~0.6 ms | 20–50 ms |
| budget panel over 10k runs' ledger entries | ~3 ms | 20–50 ms |

## Tests

The tests need PostgreSQL. They use the admin URL in `TRACKER_TEST_DATABASE_URL` if it is set.
Otherwise they start a throwaway `postgres:18.4` container, which requires Docker. Migrations
are applied once to a template database, and each test server gets a fresh copy of it, with a
super admin signed up and an API key for it (`TrackerServer.bootstrap`).

```sh
uv run python -m pytest tests/tracker
```
