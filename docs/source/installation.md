# Install Flock

Install the workspace to get the `flock` command and the Python modules used by these guides.

## Before you start

You need Git, Python 3.14 and [uv](https://docs.astral.sh/uv/). Model evaluation also needs enough memory for the model and its scoring batches. You do not need to deploy the app to use Cerulean's existing service.

## Install the workspace

1. Clone the repository and enter its root:

   ```bash
   git clone https://github.com/cerulean-works/flock.git
   cd flock
   ```

2. Install the locked workspace dependencies:

   ```bash
   uv sync --all-packages --locked
   ```

3. Verify the entry point:

   ```bash
   uv run flock --help
   uv run flock eval --help
   ```

Run the commands in these docs from the repository root. `uv run flock` uses the workspace environment; after activating `.venv`, you can use `flock` directly.

## Choose your next task

- [Sign in](cli/sign-in) for account and storage commands.
- [Run an evaluation](evals/run-evaluation) for local model scoring. A Flock account is needed only if you also log results to its tracker.
- [Run the app locally](app/run-locally) only when developing Flock itself.

S3 clients are optional dependencies of the examples that use them. Install boto3 with `uv pip install boto3`; for the Lance example, install `pylance` and `pyarrow` with `uv pip install pylance pyarrow`.
