# Deploying and operating

This page covers running Flock for a team: deploying it with Docker Compose or on its own, securing it, upgrading it, and what to watch while it runs. Every setting is listed in [Settings](configuration).

## What you deploy

A deployment has three parts:
- **One Flock process.** It serves the wandb protocol and the HTTP API on one port.
- **A PostgreSQL database.** It holds everything except uploaded run files.
- **A files directory.** It holds the files runs upload, such as `config.yaml` and `requirements.txt`.

Run exactly one Flock server per database. Flock keeps its working set in memory, so two processes writing one database would drift apart. To prevent that, it holds a PostgreSQL advisory lock while it runs, and a second Flock server on the same database refuses to start:

```text
another flock server is already using this database
```

So Flock doesn't scale out with replicas. It's built to answer a team's dashboards from one process: see [How it works](how-it-works).

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
- **Data:** two named volumes hold the data. `postgres-data` is the database, and `files` is the files directory, mounted at `/data/files`.
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

To upgrade, back up the database and the files directory, update your checkout, and rebuild:

```bash
git pull
docker compose up -d --build
```

To apply migrations yourself instead, set `FLOCK_MIGRATE_ON_STARTUP=false`. Flock then applies nothing, and refuses to start while migrations are pending:

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

## Storage and backups

- **PostgreSQL** holds organizations, users, projects and runs, with every history, system and console line, and a compact copy of finished runs' curves. Nothing is deleted except by a delete you make: there is no retention policy.
- **The files directory** holds one folder per run, named after the run's internal id. When a run is deleted, its folder is removed. If Flock stops between deleting a run and removing its folder, it removes the folder at its next start.

Back up both together. A database backup without the files directory loses the run files, and the other way round loses everything else.

## Memory and startup time

- **Run tables live in memory.** At startup, Flock loads every run's attributes, config and summary. That takes a few seconds for a project of 10,000 runs with 1,500 columns, and memory grows with runs × columns. Flock logs how long it took:

  ```text
  INFO src.core.store: loaded 10000 runs in 3 projects in 4.12s
  ```

- **Curves are cached.** The curves of active runs always stay in memory. Finished runs' curves are cached as they're read, up to `FLOCK_HISTORY_CACHE_POINTS` points in total (20,000,000 by default), and the least recently read ones are dropped first. Each cached point takes at least 12 bytes (a 4-byte line number and an 8-byte value), so the default allows at least 240 MB.
- **Database connections:** Flock opens up to `FLOCK_POOL_SIZE` (16) connections. Compose gives PostgreSQL 1 GB of `shared_buffers` (`POSTGRES_SHARED_BUFFERS`) and an `effective_cache_size` of 3 GB (`POSTGRES_EFFECTIVE_CACHE_SIZE`).

[Performance](how-it-works#performance) lists the latencies to expect.

## Crash detection

Each request in which a run's client streams its data (`file_stream`) counts as a heartbeat. When a run hasn't sent one for `FLOCK_CRASH_TIMEOUT` seconds (600 by default), Flock marks it `crashed` and logs it:

```text
INFO app: marked run flock/llm-pretrain/9fc2v9ee as crashed
```

Flock checks every `FLOCK_CRASH_TIMEOUT / 4` seconds, and at least every 30 seconds, so it notices a crash within about 1.25 timeouts. A lower timeout notices crashes sooner. But a client that can't reach Flock for longer, for example during a network outage, gets marked crashed, and returns to `running` when it sends again. The same timeout also decides how long Flock drops data from the client of a [force-deleted run](managing-runs#deleting-runs).

## Shutting down

On `SIGTERM` or `SIGINT`, Flock first ends every open event stream, because otherwise the server would wait for them forever. Dashboards reconnect when it's back, and replay what they missed. Compose gives Flock 15 seconds to stop.

## Health checks and logs

`GET /healthz` answers `{"ok":true}` without credentials. The image's Docker health check calls it.

Flock logs to standard output, at `FLOCK_LOG_LEVEL` (`info` by default). It doesn't log individual requests. Lines worth watching for:

| Log line | Meaning |
|---|---|
| `no accounts yet: open … to create the first one` | Flock has no accounts. See [The first account](accounts#the-first-account). |
| `applying migration …` | A migration is being applied at startup. |
| `loaded … runs in … projects in …s` | Startup loaded the run tables. |
| `marked run … as crashed` | Crash detection marked a run. |
| `Google sign-in: code exchange failed: …` | Google refused a sign-in. See [Setting up Sign in with Google](google-sign-in#5-check-that-it-works). |
| `crash detection failed` | The crash check raised an error. The traceback follows. |
