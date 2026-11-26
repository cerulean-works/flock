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
Each test process caps its own threads (`OMP_NUM_THREADS` and `RAYON_NUM_THREADS` at
4, `TOKENIZERS_PARALLELISM=false`), and a value set in your shell wins.

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

CI runs them on a Modal H100 (`tests/modal_gpu.py`) with eight pytest workers sharing the
GPU. Run the same thing from your machine once `modal` is logged in, passing other pytest
arguments or a worker count if you like:

```sh
uv run --no-sync modal run tests/modal_gpu.py
uv run --no-sync modal run tests/modal_gpu.py --pytest-args "-q tests/training -m 'gpu and fa4'" --workers 4
```

End-to-end tests compare models against pretrained checkpoints they download, so they
need a GPU and a Hugging Face token with access to those checkpoints (Llama 3.2 is gated).
They are excluded from both the default and `-m gpu` runs. Run them on a Modal H200 with
your local token:

```sh
HF_TOKEN=... uv run --no-sync modal run tests/modal_gpu.py --pytest-args "-q tests/training -m e2e" --workers 1 --gpu H200
```

Compiled Inductor, Triton and FlashAttention-4 kernels persist between runs in the
`flock-gpu-compile-cache` Modal Volume. Delete it with `modal volume delete
flock-gpu-compile-cache` to compile everything from scratch.
