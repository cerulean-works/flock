# Deploying and operating

This page covers running Flock for a team: deploying it with Docker Compose or on its own, securing it, upgrading it, and what to watch while it runs. Every setting is listed in [Settings](configuration).

## What you deploy

A deployment has three parts:
- **Flock replicas.** Each serves the wandb protocol and the HTTP API on one port. They keep no state of their own, so one is enough for a team, and you can run more behind a load balancer.
- **A PostgreSQL database.** It holds everything except uploaded run files.
- **Run files.** The files runs upload, such as `config.yaml` and `requirements.txt`, go to a directory or to an S3 bucket. See [Run files](#run-files).

Replicas need no sticky sessions: a dashboard's event stream reconnects to any replica and replays what it missed, and upload URLs are signed with a key every replica reads from the database. See [How it works](how-it-works).

## With Docker Compose

`app/compose.yaml` runs PostgreSQL, the API and the dashboard. The API waits until PostgreSQL is healthy. Configure it with an `.env` file next to `compose.yaml`:

```bash
POSTGRES_PASSWORD=a-long-random-password
FLOCK_WEB_URL=https://flock.example.com
FLOCK_API_PUBLIC_URL=https://api.flock.example.com
```

Then build and start it from `app`:

```bash
docker compose up -d --build
```

- **Routing:** this Compose file publishes no host ports. In Dokploy or your reverse proxy, route the dashboard domain to `ui:3000` and the SDK/API domain to `api:8080`. Both public URLs must be reachable by their clients. For local API development, `app/api/compose.dev.yaml` publishes port 8080; run `docker compose -f app/api/compose.dev.yaml up -d --build` from the repository root and `pnpm dev` from `app/web`. Its dashboard URL defaults to `http://localhost:5173`.
- **Data:** two named volumes hold the data. `postgres-data` is the database, and `files` is the files directory, mounted at `/data/files`. With the `s3` files backend, the `files` volume only holds files not moved to the bucket yet.
- **Replicas:** Compose runs one API replica. You can run more against the same database (for example with `docker compose up -d --scale api=2` behind a proxy that spreads requests), as long as they share the files volume or use the `s3` files backend.
- **S3 proxy:** the optional `s3` service, under the Compose profile `s3`, runs the [S3 proxy](s3) on port 8090 from the same image. Start it with `docker compose --profile s3 up -d` once the [S3 settings](configuration#s3-settings) are set. It keeps no state, so it can run as several replicas.
- **Settings:** use the variables below. API tuning settings pass through to the API; empty values use the defaults in [Settings](configuration).

The image installs Flock's dependencies at the versions locked in the repository's `uv.lock`. The build context is the repository root, so Docker can read the lockfile and API source together. Compose sets this for you. With plain Docker, run from the repository root:

```bash
docker build -f app/api/Dockerfile -t flock-api .
```

For deployment services, set the build context to `.` and the Dockerfile path to
`app/api/Dockerfile`. The adjacent `Dockerfile.dockerignore` includes only the API source and
dependency metadata in the context.

The image runs as an unprivileged user (uid 10001), serves on port 8080, and stores files in `/data/files`.

## Environment variables

Keep deployment values in `app/.env` (start with `app/.env.example`), or configure them in your deployment service. The three URL settings describe different network routes:

| Variable | Compose default | Meaning and consumer |
|---|---|---|
| `FLOCK_WEB_URL` | `http://localhost:3000` | Public dashboard origin, such as `https://flock.example.com`, with no path. Compose supplies it to the web image build for redirects and form origin checks, and to the API at runtime for setup, invitation, password reset and Google sign-in links. Rebuild the web image when it changes. |
| `FLOCK_API_PUBLIC_URL` | `http://localhost:8080` | Public API address that training machines reach. The API uses it for signed file upload URLs; the dashboard shows it in `wandb login --host` instructions. Set the same value for both services. Restart them when it changes. |
| `FLOCK_API_URL` | `http://api:8080` | Internal API address used only by the web server. Keep the default on the Compose network. Outside Compose it defaults to `http://127.0.0.1:8080`. Browsers and training machines never use this address. |

The SDK needs the API's wandb routes, including `/graphql`, `/files/...` and `/storage/...`. The dashboard does not proxy those routes. You can use one public origin only if your reverse proxy sends those routes to the API and dashboard routes to the web service. With two domains, route each domain to its service as shown above.

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | `flock` | Password of the Compose database user. Compose uses it for PostgreSQL and constructs the API's `FLOCK_DATABASE_URL` from it. Change it before first deployment. On an existing volume, changing this variable alone does not rotate the stored database password. |
| `FLOCK_VERSION` | `latest` | Shared image tag for `flock-api` and `flock-ui`; both images come from the same release. |
| `POSTGRES_SHARED_BUFFERS` | `1GB` | PostgreSQL's shared buffer allocation. |
| `POSTGRES_EFFECTIVE_CACHE_SIZE` | `3GB` | PostgreSQL's estimate of memory available for caching; it does not allocate that amount. |

All other API settings in [Settings](configuration#flock-settings) pass through both Compose files, except the database URL and files directory, which are fixed to the Compose database and mounted volume. This includes Google credentials, token lifetimes, Gravatar, cache limits, crash detection, CORS and logging. Optional entries are empty in Compose so their defaults are defined once in the API's `Settings`. With plain Docker or Python, set `FLOCK_DATABASE_URL` and `FLOCK_FILES_DIR` yourself.

`FLOCK_GOOGLE_CLIENT_ID` and `FLOCK_GOOGLE_CLIENT_SECRET` enable Google sign-in together. The callback defaults to `<FLOCK_WEB_URL>/api/v1/auth/google/callback`; use `FLOCK_GOOGLE_REDIRECT_URL` only when the registered callback differs. `FLOCK_JWT_SECRET` can remain unset: the API generates and persists a signing key in PostgreSQL.

### Migrating existing environment files

Rename these variables before rebuilding and restarting. Old names are no longer read:

| Previous name | Current name |
|---|---|
| `APP_URL`, `FLOCK_APP_URL`, web build argument `ORIGIN` | `FLOCK_WEB_URL` (one dashboard origin) |
| `FLOCK_PUBLIC_URL` | `FLOCK_API_PUBLIC_URL` |
| `FLOCK_URL` | `FLOCK_API_URL` |
| `UI_VERSION` | `FLOCK_VERSION` (shared by both images) |

Remove `FLOCK_DEFAULT_PROJECT`: unnamed projects always use `uncategorized`. Remove dashboard `FLOCK_API_KEY` and `FLOCK_WEB_API_KEY`: the dashboard now requires a signed-in account. Remove `FLOCK_DEFAULT_ENTITY`: setup now asks the super admin to name the first organization. Remove `APP_PORT` and `FLOCK_PORT` from the production Compose environment: it publishes no ports, so these variables have no effect. Use reverse proxy routing, or explicit `ports` entries in a Compose override for host access.

## Without Docker

Flock is a FastAPI app that needs Python 3.14. From `app/api` in a checkout of the repository:

```bash
FLOCK_DATABASE_URL=postgresql://flock:secret@db.internal:5432/flock uv run --package flock-app-api fastapi run app.py --port 8080
```

Set `FLOCK_FILES_DIR` to a directory that survives restarts: the default is `flock-files` in the working directory. Compose and the test suite use PostgreSQL 18.4.

## Behind a reverse proxy

- **Set `FLOCK_API_PUBLIC_URL`** to the address clients use, such as `https://flock.example.com`. The SDK uploads run files to URLs Flock builds from it. Without it, Flock uses the address the request reached, which behind a proxy is often an internal one.
- **Don't buffer the event stream.** Dashboards read `GET /api/v1/projects/{entity}/{project}/stream` as Server-Sent Events. Flock sends `X-Accel-Buffering: no`, which nginx honors. Turn buffering off for that path on other proxies.
- **Keep idle connections open.** The stream sends a keep-alive comment every 15 seconds when nothing else happens (`ping_seconds`). Proxy read timeouts must be longer than that.
- **Allow large uploads.** The SDK uploads run files with `PUT /storage/...`. Proxies with a request size limit, such as nginx's `client_max_body_size`, reject larger files.

## Securing it

- **Sign up first.** The first account to sign up becomes the super admin. Create it before Flock is reachable by anyone else.
- **Set `POSTGRES_PASSWORD`.** Compose defaults it to `flock`.
- **Keep the signing key safe.** Access tokens are signed with `FLOCK_JWT_SECRET`. If you don't set it, Flock uses a random key it stored in the database when it was first migrated. Changing it makes existing access tokens invalid, and clients refresh them.
- **Rate-limit sign-ins at the proxy.** Flock doesn't limit sign-in attempts. Password hashing (argon2id) is its only brake.
- **Upload URLs are signed.** The SDK uploads files to URLs that carry an HMAC signature instead of credentials. The signing key is stored in the database.

## Migrations and upgrades

The database schema is defined by SQL migrations that ship with Flock. When Flock starts, it applies the ones that are pending, each in its own transaction, and records them in the table `schema_migrations`:

```text
INFO src.db.migrate: applying migration 0004_orgs_and_users.sql
```

Some migrations leave work to Python that SQL can't do, such as reading wandb's JSON with its `NaN` values. Flock does it right after migrating, in batches it can resume, and logs its progress:

```text
backfilling the run table and series of 10000 runs
```

Concurrent replicas starting at once are safe: they take turns on a migration lock, and the second finds nothing left to do. To upgrade, back up the database and the files, update your checkout, and rebuild:

```bash
git pull
docker compose up -d --build
```

To apply migrations yourself instead, set `FLOCK_MIGRATE_ON_STARTUP=false` and run them once per deploy, before the new replicas start:

```bash
uv run --package flock-app-api python -m app.api.src.db.migrate
```

This is what a Kubernetes migration Job runs (see [On Kubernetes](#on-kubernetes)). With the setting off, Flock applies nothing, and refuses to start while migrations or their backfill are pending:

```text
database has unapplied migrations (0004_orgs_and_users) and FLOCK_MIGRATE_ON_STARTUP is off
```

**Upgrading a database from before accounts existed** (before migration `0004_orgs_and_users`): every entity becomes an organization, and existing runs have no owner, so only admins can change them. The first account to sign up becomes an admin of every organization.

### Updating an existing installation

Server and dashboard settings now use the `FLOCK_` prefix. Before restarting, set
`FLOCK_DATABASE_URL` and `FLOCK_FILES_DIR` to your existing database and uploaded files;
the naming change does not move data. The default database user, password and database name
are now `flock`, and the default local files directory is `flock-files`. Keep explicit
settings if your installation uses other names.

Access tokens now use the JWT issuer `flock`. Existing sessions can refresh their access
tokens; sign in again if your client does not refresh automatically.

## Run files

Run files go to one of two backends, chosen with `FLOCK_FILES_BACKEND`:

| Backend | Where | Replicas |
|---|---|---|
| `local` (default) | `FLOCK_FILES_DIR`, one folder per run named after its internal id | One, or several that mount the same volume, such as a `ReadWriteMany` volume on CephFS. Every write is a temporary file renamed into place. |
| `s3` | The bucket `FLOCK_FILES_BUCKET` on the S3 server set by the [S3 settings](configuration#s3-settings), under `runs/<id>/` | Any number. Flock creates the bucket if it doesn't exist. |

The SDK keeps uploading through Flock's signed URLs with either backend.

To move existing files to the `s3` backend, switch the setting and restart, then copy the directory into the bucket. Flock reads files the bucket doesn't have yet from the directory, so it keeps serving them while the copy runs. The copy skips files already in the bucket, so you can stop it and run it again:

```bash
FLOCK_FILES_BACKEND=s3 FLOCK_FILES_BUCKET=flock-files uv run --package flock-app-api python -m app.api.scripts.migrate_files
```

Add `--remove` to delete each file from the directory once it's copied.

## Storage and backups

- **PostgreSQL** holds organizations, users, projects and runs, with every history, system and console line, every run's values by column, and its curves in compact chunks. Nothing is deleted except by a delete you make: there is no retention policy. The run table takes about 250 bytes per run and column with its indexes: 3.8 GB for 10,000 runs with 1,500 columns.
- **Run files** live in one folder or prefix per run, named after the run's internal id. When a run is deleted, the background purge removes them.

Back up both together. A database backup without the run files loses them, and the other way round loses everything else.

## Memory and database load

- **Replicas hold little memory**: their open event streams and a cache of credentials. Startup takes a second or less, whatever the data, because nothing is loaded.
- **PostgreSQL's buffer cache does the caching.** Give it memory for the hot run tables and curves. Compose gives PostgreSQL 1 GB of `shared_buffers` (`POSTGRES_SHARED_BUFFERS`) and an `effective_cache_size` of 3 GB (`POSTGRES_EFFECTIVE_CACHE_SIZE`).
- **Database connections:** each replica opens up to `FLOCK_POOL_SIZE` (16) connections, plus one that listens for notifications. With a pooler such as PgBouncer in transaction mode, which drops `LISTEN`, point `FLOCK_LISTEN_DATABASE_URL` at PostgreSQL directly.
- **After a bulk load**, such as thousands of new runs, Flock vacuums and analyzes the run table itself rather than wait for autovacuum, so filters keep fast plans.

[Performance](how-it-works#performance) lists the latencies to expect.

## Crash detection

Each request in which a run's client streams its data (`file_stream`) counts as a heartbeat. When a run hasn't sent one for `FLOCK_CRASH_TIMEOUT` seconds (600 by default), Flock marks it `crashed` and logs it:

```text
INFO app: marked run flock/llm-pretrain/9fc2v9ee as crashed
```

Every replica checks every `FLOCK_CRASH_TIMEOUT / 4` seconds, and at least every 30 seconds, so a crash is noticed within about 1.25 timeouts. Each stale run is marked by one replica only. A lower timeout notices crashes sooner. But a client that can't reach Flock for longer, for example during a network outage, gets marked crashed, and returns to `running` when it sends again. The same timeout also decides how long Flock drops data from the client of a [force-deleted run](managing-runs#deleting-runs).

## On Kubernetes

Flock has no Helm chart. A deployment needs:
- **A Deployment of replicas** with `FLOCK_MIGRATE_ON_STARTUP=false`, and a Service in front of them. Any number of replicas works.
- **A migration Job** (or init container) per release that runs `python -m app.api.src.db.migrate` with the same settings, before the new replicas start.
- **Probes:** a liveness probe on `GET /healthz` and a readiness probe on `GET /readyz`.
- **Run files** on a `ReadWriteMany` volume mounted at `/data/files` by every replica, or the `s3` files backend.
- **`terminationGracePeriodSeconds`** of 30, so in-flight writes finish when a replica stops.

## Shutting down

On `SIGTERM` or `SIGINT`, a replica starts failing `/readyz`, so the load balancer stops sending it requests, and ends every open event stream, because otherwise the server would wait for them forever. Dashboards reconnect to another replica, or to this one when it's back, and replay what they missed. Requests in flight finish. Compose gives Flock 15 seconds to stop.

## Health checks and logs

`GET /healthz` answers `{"ok":true}` without credentials and without touching the database: it says the process is alive. The image's Docker health check calls it.

`GET /readyz` answers `{"ok":true}` when the replica should get traffic: its database pool answers and its connection listening for notifications is up. Otherwise, or while it shuts down, it answers 503 with `{"ok":false}`. A replica that can't listen would miss other replicas' writes, so it leaves the load balancer until it reconnects.

Flock logs to standard output, at `FLOCK_LOG_LEVEL` (`info` by default). It doesn't log individual requests. Lines worth watching for:

| Log line | Meaning |
|---|---|
| `no accounts yet: open … to create the first one` | Flock has no accounts. See [The first account](accounts#the-first-account). |
| `applying migration …` | A migration is being applied at startup. |
| `backfilling the run table and series of … runs` | A migration's backfill is running. It resumes if interrupted. |
| `marked run … as crashed` | Crash detection marked a run. |
| `Google sign-in: code exchange failed: …` | Google refused a sign-in. See [Setting up Sign in with Google](google-sign-in#5-check-that-it-works). |
| `crash detection failed` | The crash check raised an error. The traceback follows. |
| `background work failed` | Compaction, the purge or another background task raised an error. It's retried a few seconds later. |
| `listening on flock_live, … failed; retrying` | The connection listening for notifications dropped. The replica fails `/readyz` and ends its event streams until it reconnects. |
