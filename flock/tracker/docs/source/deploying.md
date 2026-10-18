# Deploying and operating

This page covers running the tracker for a team: deploying it with Docker Compose or on its own, securing it, upgrading it, and what to watch while it runs. Every setting is listed in [Settings](configuration).

## What you deploy

A deployment has three parts:
- **One tracker process.** It serves the wandb protocol and the HTTP API on one port.
- **A PostgreSQL database.** It holds everything except uploaded run files.
- **A files directory.** It holds the files runs upload, such as `config.yaml` and `requirements.txt`.

Run exactly one tracker per database. The tracker keeps its working set in memory, so two processes writing one database would drift apart. To prevent that, it holds a PostgreSQL advisory lock while it runs, and a second tracker on the same database refuses to start:

```text
another tracker server is already using this database
```

So the tracker doesn't scale out with replicas. It's built to answer a team's dashboards from one process: see [How it works](how-it-works).

## With Docker Compose

`flock/tracker/compose.yaml` runs PostgreSQL and the tracker, which waits until PostgreSQL is healthy. Configure it with an `.env` file next to `compose.yaml`:

```bash
POSTGRES_PASSWORD=a-long-random-password
TRACKER_PUBLIC_URL=https://tracker.example.com
TRACKER_APP_URL=https://flock.example.com
```

Then build and start it from `flock/tracker`:

```bash
docker compose up -d --build
```

- **Ports:** the tracker listens on port 8080 of the host. Change it with `TRACKER_PORT`.
- **Data:** two named volumes hold the data. `postgres-data` is the database, and `tracker-files` is the files directory, mounted at `/data/files`.
- **Settings:** Compose passes only some `TRACKER_*` settings through to the tracker. [Docker Compose variables](configuration#docker-compose-variables) lists them, and how to set the others.

The image installs the tracker's dependencies at the versions locked in the repository's `uv.lock`, which is at the repository root, outside `flock/tracker`. The build therefore takes the repository root as an extra build context named `workspace`. Compose passes it for you. With plain Docker, pass it yourself, from `flock/tracker`:

```bash
docker build --build-context workspace=../.. -t flock-tracker .
```

The image runs as an unprivileged user (uid 10001), serves on port 8080, and stores files in `/data/files`.

## Without Docker

The tracker is a FastAPI app that needs Python 3.14. From `flock/tracker` in a checkout of the repository:

```bash
TRACKER_DATABASE_URL=postgresql://tracker:secret@db.internal:5432/tracker uv run fastapi run app.py --port 8080
```

Set `TRACKER_FILES_DIR` to a directory that survives restarts: the default is `tracker-files` in the working directory. Compose and the test suite use PostgreSQL 18.4.

## Behind a reverse proxy

- **Set `TRACKER_PUBLIC_URL`** to the address clients use, such as `https://tracker.example.com`. The SDK uploads run files to URLs the tracker builds from it. Without it, the tracker uses the address the request reached, which behind a proxy is often an internal one.
- **Don't buffer the event stream.** Dashboards read `GET /api/v1/projects/{entity}/{project}/stream` as Server-Sent Events. The tracker sends `X-Accel-Buffering: no`, which nginx honors. Turn buffering off for that path on other proxies.
- **Keep idle connections open.** The stream sends a keep-alive comment every 15 seconds when nothing else happens (`ping_seconds`). Proxy read timeouts must be longer than that.
- **Allow large uploads.** The SDK uploads run files with `PUT /storage/...`. Proxies with a request size limit, such as nginx's `client_max_body_size`, reject larger files.

## Securing it

- **Sign up first.** The first account to sign up becomes the super admin. Create it before the tracker is reachable by anyone else.
- **Set `POSTGRES_PASSWORD`.** Compose defaults it to `tracker`.
- **Keep the signing key safe.** Access tokens are signed with `TRACKER_JWT_SECRET`. If you don't set it, the tracker uses a random key it stored in the database when it was first migrated. Changing it makes existing access tokens invalid, and clients refresh them.
- **Rate-limit sign-ins at the proxy.** The tracker doesn't limit sign-in attempts. Password hashing (argon2id) is its only brake.
- **Upload URLs are signed.** The SDK uploads files to URLs that carry an HMAC signature instead of credentials. The signing key is stored in the database.

## Migrations and upgrades

The database schema is defined by SQL migrations that ship with the tracker. When the tracker starts, it applies the ones that are pending, each in its own transaction, and records them in the table `schema_migrations`:

```text
INFO src.db.migrate: applying migration 0004_orgs_and_users.sql
```

To upgrade, back up the database and the files directory, update your checkout, and rebuild:

```bash
git pull
docker compose up -d --build
```

To apply migrations yourself instead, set `TRACKER_MIGRATE_ON_STARTUP=false`. The tracker then applies nothing, and refuses to start while migrations are pending:

```text
database has unapplied migrations (0004_orgs_and_users) and TRACKER_MIGRATE_ON_STARTUP is off
```

**Upgrading a database from before accounts existed** (before migration `0004_orgs_and_users`): every entity becomes an organization, and existing runs have no owner, so only admins can change them. The first account to sign up becomes an admin of every organization.

## Storage and backups

- **PostgreSQL** holds organizations, users, projects and runs, with every history, system and console line, and a compact copy of finished runs' curves. Nothing is deleted except by a delete you make: there is no retention policy.
- **The files directory** holds one folder per run, named after the run's internal id. When a run is deleted, its folder is removed. If the tracker stops between deleting a run and removing its folder, it removes the folder at its next start.

Back up both together. A database backup without the files directory loses the run files, and the other way round loses everything else.

## Memory and startup time

- **Run tables live in memory.** At startup, the tracker loads every run's attributes, config and summary. That takes a few seconds for a project of 10,000 runs with 1,500 columns, and memory grows with runs × columns. The tracker logs how long it took:

  ```text
  INFO src.core.store: loaded 10000 runs in 3 projects in 4.12s
  ```

- **Curves are cached.** The curves of active runs always stay in memory. Finished runs' curves are cached as they're read, up to `TRACKER_HISTORY_CACHE_POINTS` points in total (20,000,000 by default), and the least recently read ones are dropped first. Each cached point takes at least 12 bytes (a 4-byte line number and an 8-byte value), so the default allows at least 240 MB.
- **Database connections:** the tracker opens up to `TRACKER_POOL_SIZE` (16) connections. Compose gives PostgreSQL 1 GB of `shared_buffers` (`POSTGRES_SHARED_BUFFERS`) and an `effective_cache_size` of 3 GB (`POSTGRES_EFFECTIVE_CACHE_SIZE`).

[Performance](how-it-works#performance) lists the latencies to expect.

## Crash detection

Each request in which a run's client streams its data (`file_stream`) counts as a heartbeat. When a run hasn't sent one for `TRACKER_CRASH_TIMEOUT` seconds (600 by default), the tracker marks it `crashed` and logs it:

```text
INFO app: marked run flock/llm-pretrain/9fc2v9ee as crashed
```

The tracker checks every `TRACKER_CRASH_TIMEOUT / 4` seconds, and at least every 30 seconds, so it notices a crash within about 1.25 timeouts. A lower timeout notices crashes sooner. But a client that can't reach the tracker for longer, for example during a network outage, gets marked crashed, and returns to `running` when it sends again. The same timeout also decides how long the tracker drops data from the client of a [force-deleted run](managing-runs#deleting-runs).

## Shutting down

On `SIGTERM` or `SIGINT`, the tracker first ends every open event stream, because otherwise the server would wait for them forever. Dashboards reconnect when it's back, and replay what they missed. Compose gives the tracker 15 seconds to stop.

## Health checks and logs

`GET /healthz` answers `{"ok":true}` without credentials. The image's Docker health check calls it.

The tracker logs to standard output, at `TRACKER_LOG_LEVEL` (`info` by default). It doesn't log individual requests. Lines worth watching for:

| Log line | Meaning |
|---|---|
| `no accounts yet: open … to create the first one` | The tracker has no accounts. See [The first account](accounts#the-first-account). |
| `applying migration …` | A migration is being applied at startup. |
| `loaded … runs in … projects in …s` | Startup loaded the run tables. |
| `marked run … as crashed` | Crash detection marked a run. |
| `Google sign-in: code exchange failed: …` | Google refused a sign-in. See [Setting up Sign in with Google](google-sign-in#5-check-that-it-works). |
| `crash detection failed` | The crash check raised an error. The traceback follows. |
