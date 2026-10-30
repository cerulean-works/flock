# Flock app

Flock's API and dashboard track experiments through the wandb SDK and expose organization storage through an S3 proxy.

The app documentation is part of the [unified Flock docs](../../docs/source/index.md).

- [Run locally](../../docs/source/app/run-locally.md)
- [Deploy with Compose](../../docs/source/app/deploy.md#deploy-with-compose)
- [Log your first run](../../docs/source/app/log-runs.md#log-a-run)
- [Enable organization S3 storage](../../docs/source/app/deploy.md#enable-s3)
- [HTTP API reference](../../docs/source/app/api/index.md)
- [Configuration](../../docs/source/app/configuration.md)
- [Populate a demo installation](../../docs/source/app/run-locally.md#populate-a-demo-installation)

For development dependencies, use the workspace setup in the [repository README](../../README.md). The [test guide](../../tests/README.md) covers local checks. The interactive API schema is served at `/docs` and `/openapi.json`.

## Development layout

| Path | Purpose |
|---|---|
| `app.py` | FastAPI lifecycle, error handling and routers |
| `src/api/` | HTTP handlers and wandb protocol endpoints |
| `src/core/` | PostgreSQL-backed run storage, accounts, queries, curves, live events and budgets |
| `src/db/` | Database connections, migration runner and ordered SQL migrations |
| `src/s3_proxy/` | S3 signature checks and streaming proxy |
| `src/settings.py` | Environment configuration and defaults |
| `scripts/` | Demo seeding and file migration utilities |

The [architecture reference](../../docs/source/app/architecture.md) explains the design. [Upgrade instructions](../../docs/source/app/operate.md#upgrade-flock) cover applying migrations.
