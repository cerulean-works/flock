# Operate a Flock installation

Upgrade, recover and diagnose a Flock deployment. These procedures are for the operator of that installation; Cerulean users normally need only the account and experiment guides.

PostgreSQL holds account and experiment records. Uploaded run files live in a separate directory or bucket, and organization datasets live on the S3 upstream. A complete recovery plan needs the database and the matching object data. A process being alive also differs from being ready to serve traffic: `/readyz` verifies the connections required for normal operation.

## Upgrade Flock

Rebuild an existing installation while keeping its database, uploaded files and deployment settings.

Have a [recoverable backup](#back-up-and-restore), the target checkout/release and a maintenance window. Keep explicit paths and database credentials for existing installations. Updating environment variable names does not move data.

### Upgrade and verify

1. Review the target version and update your checkout through your normal release process.
2. Compare `app/.env` with the [configuration reference](configuration). Migrate obsolete names using [Environment variables](operations#environment-variables). Keep `FLOCK_DATABASE_URL` and `FLOCK_FILES_DIR` pointing at existing data for non-Compose deployments.
3. From `app`, rebuild and recreate the stack:

   ```bash
   docker compose up -d --build
   docker compose logs --tail 100 api
   ```

   If S3 is enabled, use `docker compose --profile s3 up -d --build` instead.

4. Watch for migration/backfill completion, then check `/readyz`, dashboard sign-in and a known run's curves and files.
5. Log a small new run and check that uploads and live updates still work. Check a storage read if S3 is enabled.

Migrations normally run at API startup. With `FLOCK_MIGRATE_ON_STARTUP=false`, run the migration job before starting new replicas:

```bash
uv run --package flock-app-api python -m app.api.src.db.migrate
```

The job needs the same database and storage configuration as the deployment. See [Migration reference](operations#migrations-and-upgrades) for locking, backfills and startup refusal conditions.

### Update old Compose routing

- Replace a dashboard `FLOCK_API_URL=http://api:8080` override with `http://flock-api:8080`, then recreate `api` and `ui` so the network alias and consumer agree.
- If the S3 service was named `s3`, change the domain target to `flock-s3:8090`, keep the public S3 URL, and redeploy with the profile enabled:

  ```bash
  docker compose --profile s3 up -d --build --remove-orphans
  ```

Review orphan removal before running it if your deployment uses extra Compose files. The proxy has no local data; upstream buckets remain in place.

Do not assume an older binary can use a newly migrated database. A rollback requiring an earlier schema needs the matching database and files backup.

## Back up and restore

Preserve the database and run files together so an installation can be recovered.

These commands target the bundled Compose services from `app`, using the default local run-files backend. Choose a protected backup directory outside the repository. A database dump contains account records and server signing secrets.

For the S3 files backend or organization datasets, also take a consistent backup or snapshot of the upstream buckets using your storage system's backup procedure. A database dump does not contain their objects.

### Take a consistent backup

1. Pause training clients and other writers. Stop the app services so files and database records do not change during the backup:

   ```bash
   docker compose stop api ui flock-s3
   ```

   Pause any clients writing directly to upstream organization buckets too.

2. Dump PostgreSQL to your chosen backup directory:

   ```bash
   docker compose exec -T postgres pg_dump -U flock -d flock -Fc > /path/to/backup/flock.dump
   ```

3. Archive the local files volume:

   ```bash
   docker compose run --rm -T --no-deps --entrypoint tar api -C /data/files -czf - . > /path/to/backup/run-files.tar.gz
   ```

4. Preserve the matching release identifier and deployment configuration in the protected backup location. Include upstream bucket backups when applicable.
5. Check that the dump and archive are readable before resuming service:

   ```bash
   docker compose exec -T postgres pg_restore --list < /path/to/backup/flock.dump
   tar -tzf /path/to/backup/run-files.tar.gz
   docker compose up -d api ui
   ```

   Start `flock-s3` with `docker compose --profile s3 up -d flock-s3` if it was enabled. Resume clients only after readiness succeeds.

### Restore into an empty installation

> [!CAUTION]
> Restore into a fresh recovery environment first. The database restore below replaces matching objects in the target database. Keep the app stopped until both database and files have been restored.

1. Use the same Flock release and deployment configuration, with empty database/files volumes. Start only PostgreSQL:

   ```bash
   docker compose up -d postgres
   ```

2. Wait for PostgreSQL to become healthy, then restore the dump:

   ```bash
   docker compose exec -T postgres pg_restore -U flock -d flock --clean --if-exists --exit-on-error < /path/to/backup/flock.dump
   ```

3. Restore the files archive:

   ```bash
   docker compose run --rm -T --no-deps --entrypoint tar api -C /data/files -xzf - < /path/to/backup/run-files.tar.gz
   ```

4. Restore matching upstream storage when applicable, then start the app:

   ```bash
   docker compose up -d api ui
   ```

5. Check `/readyz`, sign in, and verify a known run's metrics and a downloaded file. Check a known dataset when restoring organization storage. Start the S3 proxy when its restored upstream is ready.

A successful dump alone is not proof of recovery. Rehearse this restore with the actual backup set before relying on it. [Deployment reference](operations#storage-and-backups) describes which data lives where.

## Check health

Verify whether the app is ready for traffic and follow the component that is failing.

Know your public API and dashboard URLs. For Compose logs, work from `app` on the deployment host.

### Check readiness

1. Check the API process and database-backed readiness:

   ```bash
   curl --fail https://api.flock.example.com/healthz
   curl --fail https://api.flock.example.com/readyz
   ```

2. If either fails, inspect service state and recent logs:

   ```bash
   docker compose ps
   docker compose logs --tail 100 api postgres ui
   ```

3. Resolve the failing component, then repeat the readiness check. `/healthz` can succeed while `/readyz` returns 503: the process is alive but cannot serve normal traffic safely.

### Check a specific user problem

| Symptom | Check |
|---|---|
| Sign-in fails or API replies are unexpected | Public API route, internal `FLOCK_API_URL=http://flock-api:8080`, and matching web origin. |
| File uploads fail | `FLOCK_API_PUBLIC_URL`, public `/storage/...` route and reverse proxy upload size limits. |
| Charts stop updating | Readiness, proxy stream buffering and idle timeouts. |
| Storage is missing | S3 settings and the enabled Compose profile `s3`. |
| Browser storage transfers fail | Browser access to `FLOCK_S3_PUBLIC_URL` and proxy CORS origin `FLOCK_WEB_URL`. |
| Google login fails | Exact registered callback URL and the [Google failure reference](authentication#google-sign-in). |

After a fix, repeat the affected workflow, such as logging a run or uploading a small object. [Operation reference](operations#health-checks-and-logs) explains health endpoints and notable log lines; [Errors](errors) explains API status codes.
