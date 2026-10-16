![Cerulean Labs Flock banner](docs/assets/banner.png)

---

Flock is Cerulean Labs' monorepo for training and evaluating models. We're also
building tools to track model training and manage our datasets and model
checkpoints in our own storage infrastructure.

## Getting Started

### Installing dependencies

Install Python 3.14 and [uv](https://docs.astral.sh/uv/), then run the commands
below from the repository root. This installs all module dependencies and
development tools, with Flock in editable mode so source changes take effect
without reinstalling.

```sh
uv sync --locked
uv run python -c "import flock.training; import flock.eval; import flock.hub"
```

### Adding new dependencies

Add each dependency to the module that uses it with `uv add --package`, choosing
the package name from the commands below. Each module keeps its dependencies in
its own `pyproject.toml`, while the workspace shares one lockfile and one `.venv`.
For shared development tools, use `uv add --group dev <dependency>`.

```sh
uv add --package flock-training <dependency>
uv add --package flock-eval <dependency>
uv add --package flock-hub <dependency>
uv add --package flock-tests <dependency>
```

## Modules

### Training

[`flock.training`](flock/training/) is where we build training loops from scratch,
along with efficient implementations of optimizers, learning rate schedulers,
and other common training utilities. We want the code behind each experiment to
be easy to inspect and change, whether we're trying a different optimization
method or adjusting how a model learns from its data. Shared utilities let us
carry those improvements across experiments without rebuilding the same pieces
for every training run.

### Evaluation

[`flock.eval`](flock/eval/) is home to our in-house evaluation suite. We're building
base evaluators for tasks such as measuring perplexity and scoring multiple-choice
answers using entropy, along with a CLI for running evaluations. These evaluators
will provide reusable building blocks for new tasks and benchmarks, with a common
way to run them across models and checkpoints. The goal is to make evaluation a
routine part of experimentation, so we can understand what changes between
training runs and compare results under a consistent setup.

### Hub

[`flock.hub`](flock/hub/) is where we're building our S3-backed storage for datasets
and model training checkpoints. It aims to replace our reliance on Hugging Face
for storing and accessing these artifacts, giving us direct control over where
they live and how we organize them. We're designing it around the needs of our
training and evaluation workflows: making datasets available to experiments,
saving checkpoints as training progresses, and retrieving them for evaluation
or further training.
