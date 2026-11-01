![Cerulean Labs Flock banner](docs/assets/banner.png)

---

Flock is Cerulean's workspace for model evaluation, experiment tracking and shared
dataset and checkpoint storage.

## Documentation

Read the [Flock docs](docs/source/index.md) for task guides and references grouped
by CLI, Evals and App. To build or preview the site locally, see
[the documentation contributor guide](docs/README.md).

## Getting Started

### Installing dependencies

Install Python 3.14 and [uv](https://docs.astral.sh/uv/), then run the commands
below from the repository root. This installs all module dependencies and
development tools, with Flock in editable mode so source changes take effect
without reinstalling.

```sh
uv sync --all-packages --locked
uv run --all-packages python -c "import flock.training; import flock.eval; import flock.cli; import app.api.app"
```

### Adding new dependencies

Add each dependency to the module that uses it with `uv add --package`, choosing
the package name from the commands below. Each module keeps its dependencies in
its own `pyproject.toml`, while the workspace shares one lockfile and one `.venv`.
For shared development tools, use `uv add --group dev <dependency>`.

```sh
uv add --package flock-training <dependency>
uv add --package flock-eval <dependency>
uv add --package flock-cli <dependency>
uv add --package flock-app-api <dependency>
uv add --package flock-tests <dependency>
```

### Running tests

For the full test suite, worker limits, CPU-only CI dependencies, and timing
reports, see [Running Flock's tests](tests/README.md).

## Modules

### CLI

[`flock.cli`](flock/cli/) provides account and storage access. After installing the
workspace, run `uv run flock login` to authorize this machine with Cerulean's
service and configure the wandb SDK. [Sign-in and access](docs/source/cli/sign-in.md)
explains the credentials; [S3 storage](docs/source/cli/s3-access.md) explains
key types and renewal before connecting your tools.

### Evaluation

[`flock.eval`](flock/eval/) scores pinned language models on tasks and suites,
writes metrics and per-question predictions, and can score a model held in a
training process. [Evaluate a model](docs/source/evals/run-evaluation.md) introduces
tasks, metrics and comparable results before walking through `flock eval`.

### App

[`app/api`](app/api/) serves the wandb protocol, the read API and organization
storage; [`app/web`](app/web/) is the dashboard. Start with
[Track an experiment](docs/source/app/log-runs.md) or
[Explore experiments](docs/source/app/explore-runs.md). For local development and
separate installations, see [Run locally](docs/source/app/run-locally.md) and
[Deploy Flock](docs/source/app/deploy.md).

### Training

[`flock.training`](flock/training/) runs single-device, next-token training on
Lance text datasets. [Train with Flock](docs/source/training/trainer.md) explains
the example experiment and configuration. To evaluate a model during training,
use the [in-memory evaluation API](docs/source/evals/evaluate-checkpoint.md).
