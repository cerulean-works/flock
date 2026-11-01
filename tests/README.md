# Running tests

From the repository root:

```sh
uv sync --all-packages --locked
uv run --no-sync python -m pytest -q tests
```

Docker must be running. Tests start isolated PostgreSQL containers and local Moto
servers as needed; external services are not required.

Pytest uses up to four workers by default. Use `-n 0` for serial debugging or
`-n N` to choose a worker count. GPU and latency tests are excluded by default.

## CPU-only environment

CI uses the locked CPU-only Linux PyTorch dependencies and six workers:

```sh
uv sync --all-packages --no-group gpu --group cpu-tests --locked
uv run --no-sync python -m pytest -q tests -n 6
```

Keep `--no-sync` when running commands afterward so uv does not restore the default
GPU dependency group. Restore the development environment with
`uv sync --all-packages --locked`.

## Optional runs

Run latency benchmarks serially:

```sh
uv run --no-sync python -m pytest -q tests -m latency -n 0
```

Run GPU tests on a CUDA machine:

```sh
uv run --no-sync python -m pytest -q tests/training -m gpu -n 0
```
