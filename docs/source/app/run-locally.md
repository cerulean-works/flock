# Run Flock locally

Start an API, PostgreSQL and dashboard to develop Flock locally. To use the existing Cerulean service, [sign in](../cli/sign-in) instead.

## Before you start

You need a repository checkout, Docker with Compose v2, Python 3.14, uv and pnpm. Run the API commands from the repository root. This setup publishes development ports on your machine.

## Start and check the app

1. Start the database and API:

   ```bash
   docker compose -f app/api/compose.dev.yaml up -d --build
   ```

2. Check the API:

   ```bash
   curl --fail http://localhost:8080/healthz
   curl --fail http://localhost:8080/readyz
   ```

   Both should return `{"ok":true}`.

3. In another terminal, start the dashboard:

   ```bash
   cd app/web
   pnpm install --frozen-lockfile
   pnpm dev
   ```

4. Open `http://localhost:5173` and [create the first account](deploy#create-the-first-account). Its API address for CLI/SDK access is `http://localhost:8080`.
5. Point a test client's login at the local API and accept the SDK setup:

   ```bash
   uv run flock login --host http://localhost:8080
   ```

   [Log a first run](log-runs#log-a-run) and check it in the local dashboard.

Stop the dashboard with Ctrl-C. Stop the API and database from the repository root with `docker compose -f app/api/compose.dev.yaml down`. Without `--volumes`, Compose retains their named data volumes.

For a team deployment with public URLs and a reverse proxy, use [Deploy with Compose](deploy#deploy-with-compose). See [Settings](configuration) for local overrides.

## Populate a demo installation

Fill a disposable Flock installation with simulated experiments, users and GPU budgets so you can explore the dashboard.

Use a separate demo deployment and database. These accounts share a known password. Work from `app` in a built Compose deployment. The database must have no accounts, organizations or runs.

### Seed and inspect the demo

1. Keep PostgreSQL running, but stop the API, dashboard and optional S3 proxy. The script must be the database's only writer:

   ```bash
   docker compose stop api ui flock-s3
   ```

2. Run the seeder in a temporary API container using the deployment's database and files configuration:

   ```bash
   docker compose run --rm --no-deps --entrypoint python api -m app.api.scripts.seed_demo --scale 0.1
   ```

3. Read its completion report and the printed administrator email. Every simulated user's password is `flock-demo-password`; keep the printed key private.
4. Start the app:

   ```bash
   docker compose up -d api ui
   ```

5. Sign in with the printed account. Open a project, compare its curves, and check organization budgets and recent activity.

Without `--reset`, a nonempty database is refused. Avoid using `--reset` unless every record in the target database is disposable: it clears the database first. This procedure does not need it.

### Simulation options

| Option | Default | Meaning |
|---|---|---|
| `--scale` | `1.0` | Multiplies the number of simulated runs. `0.1` gives a smaller trial. |
| `--seed` | `0` | Random seed for a repeatable simulated workload. |
| `--keep-running` | off | Leaves some runs active; without clients, crash detection eventually marks them crashed. |
| `--database-url` | `FLOCK_DATABASE_URL`, else the API default | Database to populate. |
| `--files-dir` | `FLOCK_FILES_DIR`, else unset | Destination for simulated metadata files. |
| `--reset` | off | Clears the target database before seeding. |

The full workload spans the preceding 30 days and includes several organizations, users, projects, histories and a usage ledger. It writes directly to PostgreSQL and backfills the query structures. See [Architecture](architecture) for the storage model and [Health checks](operate#check-health) if startup fails.
