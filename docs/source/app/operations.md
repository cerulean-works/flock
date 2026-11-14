# Deployment and operation reference

Use [Deploy with Compose](deploy#deploy-with-compose), [Deploy on Docker Swarm](deploy#deploy-on-docker-swarm), [Upgrade Flock](operate#upgrade-flock) and [Back up and restore Flock](operate#back-up-and-restore) for step-by-step procedures. This page describes deployment constraints and runtime behavior.

## What you deploy

A deployment has three parts:
- **Flock replicas.** Each serves the wandb protocol and the HTTP API on one port. They keep no state of their own, so one is enough for a team, and you can run more behind a load balancer.
- **A PostgreSQL database.** It holds everything except uploaded run files.
- **Run files.** The files runs upload, such as `config.yaml` and `requirements.txt`, go to a directory or to an S3 bucket. See [Run files](#run-files).

Replicas need no sticky sessions: a dashboard's event stream reconnects to any replica and replays what it missed, and upload URLs are signed with a key every replica reads from the database. See [How it works](architecture).


## Environment variables

Keep deployment values in `app/.env` (start with `app/.env.example`), or configure them in your deployment service. The three URL settings describe different network routes:

| Variable | Compose default | Meaning and consumer |
|---|---|---|
| `FLOCK_WEB_URL` | `http://localhost:3000` | Public dashboard origin, such as `https://flock.example.com`, with no path. Compose supplies it to the web image build for redirects and form origin checks, and to the API at runtime for setup, invitation, password reset and Google sign-in links. Rebuild the web image when it changes. |
| `FLOCK_API_PUBLIC_URL` | `http://localhost:8080` | Public API address that training machines reach. The API uses it for signed file upload URLs; the dashboard shows it in `wandb login --host` instructions. Set the same value for both services. Restart them when it changes. |
| `FLOCK_API_URL` | `http://flock-api:8080` | Internal API address used only by the web server. Keep the default on the Compose network. Outside Compose it defaults to `http://127.0.0.1:8080`. Browsers and training machines never use this address. |

The API has the `flock-api` alias on the stack's default network. The dashboard uses this alias because a generic `api` hostname can resolve to another application's container when a deployment service attaches stacks to a shared ingress network. Keep the dashboard and API on their common default network. Dokploy's domain configuration still targets the Compose service `api` on port 8080.

For an existing deployment, remove any `FLOCK_API_URL=http://api:8080` override or change it to `http://flock-api:8080`. After updating the Compose file and environment, recreate both services so the API gains the alias and the dashboard uses it:

```bash
docker compose up -d --build --force-recreate api ui
```

The SDK needs the API's wandb routes, including `/graphql`, `/files/...` and `/storage/...`. The dashboard does not proxy those routes. You can use one public origin only if your reverse proxy sends those routes to the API and dashboard routes to the web service. With two domains, route each domain to its service as shown above.

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | `flock` | Password of the Compose database user. Compose uses it for PostgreSQL and constructs the API's `FLOCK_DATABASE_URL` from it. Change it before first deployment. On an existing volume, changing this variable alone does not rotate the stored database password. |
| `FLOCK_VERSION` | `latest` | Shared image tag for `flock-api` and `flock-ui`; both images come from the same release. |
| `FLOCK_S3_WORKERS` | `4` | Worker processes in the `flock-s3` service. Each one uses at most one CPU core, so raise it for heavy upload or download traffic. Each worker also opens up to `FLOCK_S3_POOL_SIZE` database connections plus one, so keep the total under PostgreSQL's `max_connections` (see [Database connections](#database-connections)). |
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

Concurrent replicas starting at once are safe: they take turns on a migration lock, and the second finds nothing left to do. Each migration waits at most 5 seconds for a table lock (`lock_timeout`). If the lock isn't free by then, for example behind a long query, Flock waits a few seconds and tries again, up to 10 times, and then fails to start. Without the timeout, a migration waiting for its lock would hold up every query behind it. To upgrade, back up the database and the files, update your checkout, and rebuild:

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

A migration file whose first line is `-- flock: no-transaction` runs outside a transaction, for statements that can't run in one, such as `CREATE INDEX CONCURRENTLY`. Such a file holds one statement, written so it can run again (`IF NOT EXISTS`), because a failure partway can't be rolled back.

**Upgrading a database from before accounts existed** (before migration `0004_orgs_and_users`): every entity becomes an organization, and existing runs have no owner, so only admins can change them. The first account to sign up becomes an admin of every organization.

### Expand and contract migrations

On a [Swarm stack](deploy#deploy-on-docker-swarm), the new API applies its migrations while the old release still serves, for a minute or more. So a migration must not remove anything the old release reads. Migrations come in two kinds, in one numbered sequence:

| Kind | File | What it does | When Flock applies it |
|---|---|---|---|
| Expand | `NNNN_name.sql` | Only adds: tables, nullable columns or columns with a default, indexes, functions, data copied into new columns. The previous release keeps working on the schema it leaves. | On startup, as above. |
| Contract | `NNNN_name.contract.sql` | Removes what an earlier expand replaced: drops a column, table or sync trigger, or adds `NOT NULL` once every row is filled. | Once no running replica predates it, and that has been true for `FLOCK_CONTRACT_DELAY`. |

Every API process and S3 proxy worker records which release it runs, and refreshes the record every 15 seconds. A record not refreshed for 60 seconds counts as gone. A contract numbered `NNNN` is due once every live process knows `NNNN`, and has been for `FLOCK_CONTRACT_DELAY` seconds (`1800`, 30 minutes, by default). The delay leaves time to roll a bad release back to the previous one while that release still works on the schema. Every API process checks every minute and applies due contracts under the migration lock, so only one applies each. The S3 proxy applies no migrations.

A change that breaks old code, such as renaming a column, takes two releases. The first ships an expand that adds the new column, copies the data and keeps both columns in step with a trigger. A later one ships the contract that drops the trigger and the old column. Keep the columns in step in the database, not in a release's code: a deployment that only runs the latest release may skip the one in between.

With `FLOCK_MIGRATE_ON_STARTUP=false`, nothing applies contracts by itself. Apply the due ones from your migration job, after the new replicas are up:

```bash
uv run --package flock-app-api python -m app.api.src.db.migrate --contract
```

With `--contract --force`, it applies every pending contract now, without waiting for old replicas or the delay. Use it only on a database nothing else is using.

A release refuses to start on a database where a contract newer than the release has been applied, rather than serve errors on what the contract dropped:

```text
database has contract migrations this release doesn't know (0022_drop_project_name): deploy a release from 0022 or later
```

Expand migrations a release doesn't know don't stop it, since they only add. This is what makes a rollback past the contract delay fail, and Swarm then keeps the newer release running.

CI checks every expand file new on a branch for statements that break the previous release: `DROP TABLE`, `DROP COLUMN`, `RENAME`, `ALTER COLUMN ... TYPE`, `SET NOT NULL`, and `ADD COLUMN ... NOT NULL` without a default. Those belong in a contract file. A statement that must run at expand time, such as dropping a column no release ever read, passes with a comment on the line above it:

```sql
-- flock: allow-breaking the column was never read
ALTER TABLE projects DROP COLUMN legacy_note;
```

Say in the pull request that the deploy has a short window in which the old release can see errors.

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
- **Database connections:** see [Database connections](#database-connections).
- **After a bulk load**, such as thousands of new runs, Flock vacuums and analyzes the run table itself rather than wait for autovacuum, so filters keep fast plans.

[Performance](architecture#performance) lists the latencies to expect.

### Database connections

Each process keeps a pool of connections. It opens them as requests need them, up to the pool's size, and keeps a few open when idle. Each process also keeps one more connection open, to listen for notifications.

| Process | Pool size | Most connections |
|---|---|---|
| Each API replica | `FLOCK_POOL_SIZE` (16) | 16 + 1 = 17 |
| Each S3 proxy worker | `FLOCK_S3_POOL_SIZE` (4) | 4 + 1 = 5 |

The default stack, one API replica and `FLOCK_S3_WORKERS` (4) proxy workers, uses at most 17 + 4 × 5 = 37 connections. During a [Swarm redeploy](deploy#deploy-on-docker-swarm) the old and new release run side by side, which doubles it to 74. That fits under PostgreSQL's default `max_connections` of 100, with room for a backup or a `psql` session. If you raise worker counts, replicas or pool sizes, count the overlap too, and raise `max_connections` if needed.

With a pooler such as PgBouncer in transaction mode, which drops `LISTEN`, point `FLOCK_LISTEN_DATABASE_URL` at PostgreSQL directly.


## Crash detection

Each request in which a run's client streams its data (`file_stream`) counts as a heartbeat. When a run hasn't sent one for `FLOCK_CRASH_TIMEOUT` seconds (600 by default), Flock marks it `crashed` and logs it:

```text
INFO app: marked run flock/llm-pretrain/9fc2v9ee as crashed
```

Every replica checks every `FLOCK_CRASH_TIMEOUT / 4` seconds, and at least every 30 seconds, so a crash is noticed within about 1.25 timeouts. Each stale run is marked by one replica only. A lower timeout notices crashes sooner. But a client that can't reach Flock for longer, for example during a network outage, gets marked crashed, and returns to `running` when it sends again. The same timeout also decides how long Flock drops data from the client of a [force-deleted run](run-management#deleting-runs).


## On Kubernetes

Flock has no Helm chart. A deployment needs:
- **A Deployment of replicas** with `FLOCK_MIGRATE_ON_STARTUP=false`, and a Service in front of them. Any number of replicas works.
- **A migration Job** (or init container) per release that runs `python -m app.api.src.db.migrate` with the same settings, before the new replicas start.
- **A contract Job** that runs `python -m app.api.src.db.migrate --contract` after the old replicas are gone, such as a CronJob every few minutes. It applies only the [contracts](#expand-and-contract-migrations) that are due.
- **Probes:** a liveness probe on `GET /healthz` and a readiness probe on `GET /readyz`.
- **Run files** on a `ReadWriteMany` volume mounted at `/data/files` by every replica, or the `s3` files backend.
- **`terminationGracePeriodSeconds`** of 30, so in-flight writes finish when a replica stops.


## Shutting down

On `SIGTERM` or `SIGINT`, a replica starts failing `/readyz`, so the load balancer stops sending it requests, and ends every open event stream, because otherwise the server would wait for them forever. Dashboards reconnect to another replica, or to this one when it's back, and replay what they missed. Requests in flight finish. How long a process gets before it's killed:

| Deployment | API | S3 proxy |
|---|---|---|
| Compose | 15 seconds | 10 seconds (Docker's default) |
| Swarm stack | 30 seconds | 120 seconds |

On a stack, the API's 30 seconds cover a large `file_stream` batch or upload. The S3 proxy stops taking connections and lets open downloads run for 2 minutes. A download still running after that is cut, and the client sees a dropped connection. Clients that read in ranged chunks, such as s3fs, retry the chunk. boto3 doesn't retry a broken body by itself.


## Health checks and logs

`GET /healthz` answers `{"ok":true}` without credentials and without touching the database: it says the process is alive. The image's Docker health check calls it.

`GET /readyz` answers `{"ok":true}` when the replica should get traffic: its database pool answers and its connection listening for notifications is up. Otherwise, or while it shuts down, it answers 503 with `{"ok":false}`. A replica that can't listen would miss other replicas' writes, so it leaves the load balancer until it reconnects.

Flock logs to standard output, at `FLOCK_LOG_LEVEL` (`info` by default). It doesn't log individual requests. Lines worth watching for:

| Log line | Meaning |
|---|---|
| `no accounts yet: open … to create the first one` | Flock has no accounts. See [The first account](authentication#the-first-account). |
| `applying migration …` | A migration is being applied at startup. |
| `backfilling the run table and series of … runs` | A migration's backfill is running. It resumes if interrupted. |
| `marked run … as crashed` | Crash detection marked a run. |
| `Google sign-in: code exchange failed: …` | Google refused a sign-in. See [Setting up Sign in with Google](authentication#failure-reasons). |
| `crash detection failed` | The crash check raised an error. The traceback follows. |
| `background work failed` | Compaction, the purge or another background task raised an error. It's retried a few seconds later. |
| `listening on flock_live, … failed; retrying` | The connection listening for notifications dropped. The replica fails `/readyz` and ends its event streams until it reconnects. |
