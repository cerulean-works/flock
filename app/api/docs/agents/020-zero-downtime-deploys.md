# 020: redeploys without a gap, on Docker Swarm

| | |
| --- | --- |
| Status | built, not yet deployed (section 6 is still to check on the server) |
| Date | 2026-11-14 |
| Builds on | [009](009-s3-proxy.md), [012](012-postgres-only.md) |
| Amends | D83 (migrations gain a contract step, applied once no old replica is left) |
| Keeps | everything else before D138 |

## 1. Goal

Every redeploy in Dokploy leaves a few seconds to a few tens of seconds in which Traefik answers
404 for the API domain and the S3 domain. The wandb SDK doesn't retry a 404, so every run that is
logging at that moment crashes, and so does every job streaming a dataset or checkpoint through
the S3 proxy.

After this stage:

1. A redeploy always has a healthy API and a healthy S3 proxy behind Traefik. The new one starts,
   passes its health check, takes traffic, and only then is the old one stopped.
2. If the new one never becomes healthy, the old one keeps serving and the deploy rolls back.
3. The worst a client can see during a redeploy is a dropped connection or a 502/503 on a
   request that was already in flight, which the SDK and S3 clients retry. Never a 404.
4. Old and new releases can share the database for as long as they overlap. Migrations are split
   into an expand step that the new release applies on startup and a contract step that Flock
   applies by itself once no old replica is left.

The deploy work is in a Swarm version of `app/compose.yaml`, a CI job that publishes images, and
the Dokploy settings. The migration work is in `app/api/src/db/migrate.py` and the servers'
startup.

## 2. Why the gap happens today

Flock is a Dokploy Compose service of the "Docker Compose" type, so a deploy runs
`docker compose up -d --build`. For a service whose image or config changed, Compose stops the
old container and then creates the new one. Compose has no start-first mode: `deploy.update_config`
is ignored outside Swarm.

Traefik reads Compose services through its Docker provider, which builds routers from running
containers and skips a container whose health check is still `starting`. So the router for
`api` disappears when the old container exits and comes back only when the new one is healthy.
The image's health check runs every 10 seconds, so that's at least one interval plus startup
and any migration. With no router, Traefik answers 404. The same holds for `flock-s3`.

Running two replicas under Compose doesn't fix it, because Compose recreates every replica of a
changed service in the same step.

## 3. Design decisions

### D138. Flock runs as a Dokploy "Stack" (`docker stack deploy`)

Dokploy's Compose service has two types: Docker Compose and Stack. Stack deploys the same file
with `docker stack deploy`, so each service is a Swarm service and its `deploy` section applies.
Dokploy itself already runs in Swarm mode, so the server needs no new setup.

A new file, `app/compose.swarm.yaml`, is the stack. `app/compose.yaml` stays as it is for local
runs and for operators who don't use Swarm. Two files that mostly repeat each other is a cost,
but the differences (no `build`, labels under `deploy`, no `depends_on`, no `profiles`) are too
many for an override file to express cleanly.

Rejected:
- **Stay on Compose with a rollout script** such as `docker-rollout`, which scales a service to
  two, waits, then removes the old container. Dokploy runs its own deploy command, so this would
  mean deploying outside Dokploy, and Traefik's Docker provider still has a window between the
  old container stopping and Traefik's next refresh.
- **Blue and green Compose projects** switched by hand in Traefik. Works, but every deploy
  becomes a manual step.

### D139. Each changed service starts first and is checked before the old one stops

The API and the S3 proxy get:

```yaml
deploy:
  replicas: 1
  update_config:
    order: start-first
    parallelism: 1
    failure_action: rollback
    monitor: 30s
  rollback_config:
    order: start-first
  restart_policy:
    condition: any
```

Swarm starts the new task, waits until its health check passes, keeps watching it for `monitor`
(30 seconds), and then stops the old task. If the new task fails its health check or exits in
that time, Swarm rolls the service back to the previous spec and the old task never stopped.

The health checks stay the ones the images already have (`/healthz`), except that the API's
`start_period` goes from 60 seconds to 15 minutes. The API applies migrations and their backfill
before it serves (D83), and a failed check during a long backfill would otherwise roll the
deploy back halfway. Failures during `start_period` don't count, and the task is healthy at its
first passing check, so a normal start isn't slowed down. Swarm keeps a task out of its
service's load balancer until it's healthy.

The dashboard (`ui`) gets the same `update_config`. It isn't what crashes runs, but it's free.
PostgreSQL keeps the default stop-first order, with one replica, because two PostgreSQL
processes must never share a data directory (D144).

### D140. Traefik routes to the Swarm service address (`lbswarm`)

Traefik's Swarm provider, by default, routes to each task's own IP and refreshes its list every
15 seconds. During a start-first update that leaves up to 15 seconds in which Traefik still sends
requests to a task that has already been told to stop.

With `traefik.docker.lbswarm=true`, Traefik routes to the service's virtual IP instead, and
Swarm's own load balancer picks the task. Swarm takes a task out of the virtual IP before it
sends it `SIGTERM`, and adds the new one only once it's healthy, so the switch is immediate.
The router also exists for as long as the service does, so it can never 404 during a deploy.

Labels go under `deploy.labels`, as Swarm requires:

```yaml
deploy:
  labels:
    - traefik.enable=true
    - traefik.docker.lbswarm=true
    - traefik.http.routers.flock-api.rule=Host(`api.flock.example.com`)
    - traefik.http.routers.flock-api.entrypoints=websecure
    - traefik.http.routers.flock-api.tls.certresolver=letsencrypt
    - traefik.http.services.flock-api.loadbalancer.server.port=8080
```

Whether Dokploy's Domains tab injects these for a Stack is not documented, so the labels are
written in the file (with the hosts from env), and the Domains tab entries for Flock are removed
at cutover. Router names are prefixed `flock-` so they can't clash with other stacks.

Swarm's load balancer keeps an open connection on the task it started on, so a dashboard's event
stream stays on the old task until that task drains it (D142) and the dashboard reconnects.

### D141. CI publishes the images, and the stack pulls them by version

`docker stack deploy` ignores `build`, so the images have to come from a registry. A new GitHub
Actions workflow builds `flock-api` and `flock-ui` on every push to `main` and pushes them to
GHCR as `ghcr.io/cerulean-works/flock-api` and `flock-ui`, tagged with the commit SHA and
`main`.

The stack uses `${FLOCK_VERSION}` as the tag, and the Dokploy deploy is pinned to a SHA, not to
`main`. A moving tag makes Swarm unsure whether anything changed, and makes rollback point at
the wrong image. The workflow ends by calling Dokploy's deploy webhook with the new SHA, so a
merge to `main` still deploys on its own, as today.

`FLOCK_WEB_URL` is baked into the dashboard image, so the workflow passes it as a build
argument from a repository variable. Dokploy needs read access to GHCR (a token with
`read:packages`), and the stack is deployed with `--with-registry-auth`.

### D142. Old tasks get time to finish what they're doing

When Swarm stops an old task it sends `SIGTERM`, waits `stop_grace_period`, then kills it.

- **API:** 30 seconds (from 15). On `SIGTERM` it already fails `/readyz` and ends its event
  streams (see "Shutting down" in the operations reference), and requests in flight finish.
  30 seconds covers a large `file_stream` batch or upload.
- **S3 proxy:** 120 seconds. A `GET` of a large object streams for as long as the client reads.
  uvicorn stops accepting on `SIGTERM` and waits for open responses until the grace period ends.
  A download still running after 2 minutes is cut, and the client sees a dropped connection
  mid-body (see gaps).

### D143. The S3 proxy gets a small pool of its own

Each process holds a pool of connections, not one. An asyncpg connection runs one query at a
time, so a single connection would make every concurrent request wait in line behind the
others, including the SDK's `file_stream` calls behind a slow dashboard query. The pool opens
connections as requests need them, up to its size, and keeps a couple open when idle. Each
process also keeps one more connection open for `LISTEN`.

So an idle stack holds few connections: the API keeps 2 plus the listener, and each of the 4 S3
proxy workers keeps 1 plus its listener, about 13 in all. The numbers that matter are the caps
under load. Today those are 16 + 1 for the API, and 4 × (16 + 1) = 68 for the proxy, because the
proxy reuses `FLOCK_POOL_SIZE`. That's 85, against PostgreSQL's default `max_connections` of
100. During a start-first update both releases are up, which could reach 170.

The proxy doesn't need 16 connections per worker. Its database work is looking up keys, roles
and bucket policies, cached for 10 seconds and dropped on a notification, plus a write when an
upload starts or ends. A new setting, `FLOCK_S3_POOL_SIZE`, defaulting to 4, sets its pool. The
API keeps `FLOCK_POOL_SIZE` (16).

That makes the caps 17 for the API and 4 × 5 = 20 for the proxy, 37 in all, and 74 while both
releases overlap. That fits under 100 with room for a backup or a `psql` session, so
PostgreSQL's settings don't change. The operations reference gets the count with the overlap,
for operators who raise worker counts.

Rejected: a pooler such as PgBouncer in front of PostgreSQL. It would cap connections whatever
the replica count, but it's another service to run, and Flock's `LISTEN` connection would still
have to bypass it (`FLOCK_LISTEN_DATABASE_URL`). Worth it with more replicas, not with one.

### D144. PostgreSQL stays in the stack, on its existing volume

PostgreSQL stays a service of the stack, with one replica, stop-first, and a placement
constraint on the manager node so its volume never moves. A redeploy doesn't touch it unless its
spec changes, since the tag `postgres:18.4` is fixed.

Stacks prefix volume names with the stack name, which differs from the Compose project name. So
the data volume is declared `external` with its current name, and the stack keeps using the
data PostgreSQL already has:

```yaml
volumes:
  postgres-data:
    external: true
    name: <current compose project>_postgres-data
  files:
    external: true
    name: <current compose project>_files
```

Rejected: moving PostgreSQL to a Dokploy Database service. It would take the database out of
every Flock redeploy for good, but it needs a dump and restore, and it isn't needed for this
goal.

### D145. Services find each other without `depends_on`

Swarm ignores `depends_on`. The ordering it gave is replaced by restarts:
- The S3 proxy and the dashboard start whether or not the API is up. The proxy fails its health
  check until the API has applied migrations, and Swarm restarts it. This happens only on the
  first deploy or one with a migration.
- `flock-api` stays an alias of the API on the stack network, so the dashboard's
  `FLOCK_API_URL` doesn't change.
- All three app services join `dokploy-network` (external, overlay) for Traefik, and a private
  overlay network for PostgreSQL. PostgreSQL is only on the private one.

## 4. Expand and contract migrations

With start-first, the new API applies its migrations while the old release is still serving:
for its own startup, the 30 seconds of `monitor`, and the old task's grace period. A migration
that drops or renames something the old release reads breaks the old release for that time. A
rule alone ("only add things") holds until someone forgets it, so the runner splits migrations in
two and enforces when each half runs.

### D146. A migration has an expand file and, if it needs one, a contract file

Migrations stay numbered SQL files in one sequence. A file is one of two kinds:

- **Expand**, `NNNN_name.sql`, as today. It only adds: tables, nullable columns or columns with
  a default, indexes, functions, and data copied into new columns. The release before it must
  keep working on the schema it leaves. It's applied on startup, as today.
- **Contract**, `NNNN_name.contract.sql`. It removes what an earlier expand replaced: drops a
  column or table, adds `NOT NULL` once every row is filled, drops a sync trigger. It's applied
  only once no replica of a release older than this file is still running (D147).

A change that breaks old code takes two releases. For example, renaming `projects.name` to
`display_name`:

1. Release A ships `0020_project_display_name.sql`, which adds `display_name`, copies `name`
   into it, and adds a trigger that keeps both in step on write. Release A's code reads and writes
   `display_name`. Release before A, still running during the deploy, keeps using `name`, and the
   trigger keeps `display_name` current for it.
2. Release B (or A itself, if nothing still needs `name` for writes) ships
   `0021_drop_project_name.contract.sql`, which drops the trigger and `name`.

Data copies too slow for one transaction go in the backfill (`db/backfill.py`), which is already
batched and resumable, rather than in the expand file.

### D147. Replicas say which release they run, and contracts wait for the oldest

A new table lists the processes using the database:

```sql
CREATE TABLE flock_instances (
    id uuid PRIMARY KEY,
    service text NOT NULL,       -- 'api' or 's3'
    knows text NOT NULL,         -- the highest migration number in this release
    started_at timestamptz NOT NULL DEFAULT now(),
    seen_at timestamptz NOT NULL DEFAULT now()
);
```

Every API process and S3 proxy worker inserts its row on startup, updates `seen_at` every 15
seconds from a background task (next to the crash watcher), and deletes it on shutdown. A row not
seen for 60 seconds counts as gone, which covers a killed process.

A contract file numbered `NNNN` is due once two things are true:
1. Every live instance knows `NNNN`, so no running code predates the contract.
2. That has been true for `FLOCK_CONTRACT_DELAY` (30 minutes by default). This leaves time to
   roll a bad release back to the previous one while that release still works on the schema.

Every API process checks every minute and applies due contracts under the same advisory lock as
the expand migrations, so only one applies each. The S3 proxy doesn't apply migrations, as today.
`python -m app.api.src.db.migrate --contract` applies due contracts once, for operators who turn
off `FLOCK_MIGRATE_ON_STARTUP` (Kubernetes jobs). `--force` skips both conditions, for a
database nothing else is using.

Rejected: applying a release's contracts on the next release's startup. It's simpler, but it
has the same problem the start-first update has: the release before is still serving while the
next one starts, and nothing would know when the one before that is really gone.

### D148. Old code refuses a database that has been contracted past it

A release starting against a database with an applied contract numbered above its own highest
migration exits with a clear error, instead of serving errors on whatever the contract dropped:

```text
database has contract migrations this release doesn't know (0021_drop_project_name): deploy a release from 0021 or later
```

Expand migrations it doesn't know don't stop it, since they're compatible by D146. This is what
makes an old rollback after the contract delay fail loudly, and Swarm's `failure_action:
rollback` then keeps the newer release running.

### D149. The runner protects the old release while it migrates

Two changes to how files are applied:

- **A lock timeout.** Each migration runs with `SET LOCAL lock_timeout = '5s'`. An `ALTER TABLE`
  waiting behind a long query would otherwise queue every query after it, including all of the
  old release's, until the long one ends. With the timeout it fails, the runner waits a few
  seconds and tries again, up to 10 times, then the API fails to start and the deploy rolls back.
- **No transaction on request.** `CREATE INDEX CONCURRENTLY` can't run in a transaction, and a
  plain `CREATE INDEX` on a big table blocks writes for its duration. A file whose first line is
  `-- flock: no-transaction` runs outside one. Such a file must contain one statement written to
  be retried (`IF NOT EXISTS`), since a failure partway can't be rolled back.

### D150. CI checks that expand files only expand

A test reads every expand file added on the branch and fails on statements that break the
previous release: `DROP TABLE`, `DROP COLUMN`, `RENAME`, `ALTER COLUMN ... TYPE`, `SET NOT
NULL`, and `ADD COLUMN ... NOT NULL` without a default. It's a text check, so it can be fooled,
but it catches the usual mistake. Those statements belong in a contract file.

A statement that really must run at expand time (say, a column that was never read) is allowed
with a `-- flock: allow-breaking <reason>` comment on the line above. The PR then says the
deploy has a short window of errors for the old release.

Rejected for now: running the previous release's test suite against the new schema in CI. It's
the stronger check, but it means building and running two releases per PR. Worth adding if the
text check lets something through.

Existing files `0001` to `0018` are all treated as expand files, already applied everywhere, and
the check only looks at files new on the branch.

## 5. Plan

1. `app/compose.swarm.yaml` with the services, labels, update and rollback config, health check
   start period, grace periods, networks and external volumes above. Hosts and the image owner
   come from env.
2. `FLOCK_S3_POOL_SIZE` (D143) in the settings, the proxy and both Compose files.
3. The migration runner: contract files, `flock_instances` and its heartbeat, the contract check
   in the API, the startup refusal, `lock_timeout` with retries, `no-transaction`, and
   `migrate --contract` / `--force` (D146 to D149). Tests in `tests/app` for each, including two
   "releases" (two migration directories) sharing one database.
4. The CI check on expand files (D150) as a test in `tests/app`.
5. `.github/workflows/publish-images.yaml`: build both images on push to `main`, push to GHCR
   with the SHA and `main` tags, then call the Dokploy deploy webhook with `FLOCK_VERSION` set
   to the SHA.
6. Docs: a "Deploy on Docker Swarm" section in `deploy.md`, and in `operations.md` the
   connection count with the overlap, expand and contract migrations, `FLOCK_CONTRACT_DELAY`,
   and the new grace periods. A short README in the migrations folder for whoever writes one.
7. Test on a throwaway Dokploy stack on the same server (or a scratch VM with Dokploy): run a
   script that logs a wandb run at 10 steps a second and another that loops `GET`s of a 1 GB
   object through the proxy, redeploy three times, and check neither crashes. Deploy a release
   with an expand and a contract (the rename above, on a scratch table) and check the contract
   waits for the old task to go and for the delay. Then deploy an image whose health check
   fails and check the old task keeps serving and the deploy rolls back.
8. Cutover, below.

### Cutover (one planned gap)

Moving from Compose to a stack can't be seamless, since both would bind the same Traefik hosts
and the same PostgreSQL volume. Pick a quiet time and announce it:

1. Note the current Compose project name and its volume names (`docker volume ls`), and take a
   database dump and a files backup.
2. In Dokploy, stop the Compose service and remove its domains.
3. Create the Stack service (or switch the type) with `compose.swarm.yaml`, the same env, and
   `FLOCK_VERSION` set to the current SHA's image. Deploy.
4. Check `/readyz` on the API and `/healthz` on the S3 domain, sign in, log a test run, list a
   bucket.
5. Redeploy once with no change and once with a new SHA while a test run logs, to see it
   survive.

## 6. To check on the server before building

These aren't documented by Dokploy, so I'd confirm them on our server first:

- Dokploy's Traefik config (`/etc/dokploy/traefik/traefik.yml`) enables the `swarm` provider,
  not only `docker`. If it doesn't, the stack's labels are never read.
- The name of the TLS cert resolver and the entrypoints in that config, for the labels.
- `dokploy-network` is an attachable overlay network (`docker network inspect dokploy-network`).
- Whether the server is a single Swarm node. The design assumes one node; with more, the `files`
  volume needs the `s3` files backend (operations reference, "Run files") and PostgreSQL needs
  its placement constraint.

## 7. Known gaps after this stage

- A download through the S3 proxy that runs longer than 2 minutes when the deploy happens is cut
  mid-body. boto3 doesn't retry a broken body by itself. Clients that read in ranged chunks
  (s3fs, most dataset loaders) retry the chunk. Raising the grace period further only delays
  redeploys.
- A dashboard's event stream on the old task is ended and reconnects, as on any shutdown today.
- Expand and contract makes breaking changes possible without a gap, not automatic: the author
  still has to write the dual-write code or trigger for the release in between.
- PostgreSQL itself still has a gap when it's upgraded. That's rare and planned.
- One replica of each service still means a crash of the API (not a deploy) gives a gap until
  Swarm restarts it. Going to two replicas is a one-line change, and the connection count in D143
  leaves room for it, but it isn't part of this stage.

## 8. How it was actually built

Where the build differs from the sections above:

- **When the contract delay starts (D147).** A second table, `flock_contract_waits`, keeps since
  when every live instance has known each contract. A check that finds an instance which doesn't
  know it removes the row, and so does such an instance when it registers, so one that comes and
  goes between two checks still restarts the delay. Registering and applying a contract take a
  second advisory lock (`LOCK + 1`), so a contract never runs while a server that doesn't know
  it is starting.
- **The S3 proxy refuses an unknown contract too (D148).** Its workers register like API
  processes, and registering runs the same check. A proxy that starts before the API has
  applied `0019_instances` fails and is restarted, as D145 expects.
- **`migrate --contract` only applies contracts.** It stops at an expand file not applied yet.
  With `FLOCK_MIGRATE_ON_STARTUP` off, API processes don't apply contracts, so the operator runs
  it from time to time.
- **The expand-file check (D150) needs no git.** It checks every expand file numbered `0019` and
  above, in `tests/app/test_expand_files.py`.
- **The deploy step goes through Dokploy's API, not its webhook (D141).** The webhook takes no
  environment, so the workflow reads the stack's env with `compose.one`, replaces the
  `FLOCK_VERSION` line, writes it back with `compose.update`, and calls `compose.deploy`. It's
  skipped until the `DOKPLOY_URL`, `DOKPLOY_API_KEY` and `DOKPLOY_COMPOSE_ID` secrets are set.
- **Both Traefik label families (D140).** The stack sets `traefik.swarm.*` (Traefik v3's Swarm
  provider) and `traefik.docker.*` (a v2 Docker provider in Swarm mode). Whichever doesn't apply
  is ignored.
