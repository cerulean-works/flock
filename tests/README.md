# Running Flock's tests

From the repository root, install the workspace and run the full suite:

```sh
uv sync --all-packages --locked
uv run --no-sync python -m pytest -q tests
```

The default is up to four local worker processes. Each worker starts its own
throwaway PostgreSQL container and local Moto server when needed. Docker must
be running. No external database or S3 service is needed. Existing
`FLOCK_TEST_DATABASE_URL` and `FLOCK_TEST_S3_*` overrides still work; only point
them at services you are authorized to use.

Use `-n 0` for serial debugging or `-n 2` to match CI's worker count. Set
`PYTEST_XDIST_AUTO_NUM_WORKERS=2` to lower the automatic local limit. An explicit
`-n N` overrides automatic detection. Tests sharing `make_module_flock` stay on
one worker per module; independent tests can run on different workers. New
module-scoped mutable fixtures need the same grouping or an explicit
`pytest.mark.xdist_group` marker.

Latency benchmarks remain excluded from the default suite. Run them without
other test workers contending for resources:

```sh
uv run --no-sync python -m pytest -q tests/app/test_latency.py -m latency -n 0
```

## CPU dependencies in CI

The optional `cpu-tests` group selects the locked CPU-only Linux PyTorch wheel.
The default `gpu` group preserves the usual PyPI build. The groups are mutually
exclusive; choose the CPU group explicitly for a test environment:

```sh
uv sync --all-packages --no-group gpu --group cpu-tests --locked
uv run --no-sync python -m pytest -q tests -n 2
```

Use `--no-sync` after that installation so `uv run` does not restore the default
GPU dependency group. To restore the regular development environment, run
`uv sync --all-packages --locked` again. CI verifies that PyTorch has no CUDA
runtime, reports the uv cache hit, and uploads JUnit results and setup/call/teardown
durations as the `pytest-results` artifact, including on test failure.

## Timing and fixture conventions

```sh
uv run --no-sync python -m pytest -q tests --durations=0 --durations-min=0.01 --junitxml=/tmp/flock-tests.xml
```

Compare three sequential full-suite runs on the same machine, without running
other test suites alongside them. Check both the median wall time and identical
test outcomes. Session fixture costs appear against the first or last test using
them, so a single test's reported time may include shared service setup or teardown.

The `clock` fixture binds a controllable wall clock to one module's `time` name.
It does not replace the process-wide clock, async scheduling, or PostgreSQL's
`now()`. Use explicit database timestamps for SQL expiry conditions. Billing
tests advance the store clock between real heartbeat requests; credential tests
advance the consumer clock while retaining real issuance and refresh callbacks.
Use explicit completion of background writes instead of arbitrary sleeps.

PR #49 identified races caused by very short JWT lifetimes and a crash event
published before the dashboard subscribed. The JWT test checks the configured
lifetime and submits a correctly signed expired token. The crash-announcement
test keeps heartbeat age fixed until the stream's `hello` confirms subscription,
then advances the store clock; the real background watcher publishes the event.

The shared W&B script helper uses a one-second transmit interval. The installed SDK's Go backend
truncates a fractional interval such as `0.1` to zero, which selects the
15-second default. Real SDK sampling and crash-detection tests still exercise
their asynchronous behavior. Ordinary API tests retain real sockets, application
lifespans, and isolated databases; an ASGI transport migration is deferred until
profiling justifies it.

## Earlier measurements on 2026-10-29

These measurements were taken in the earlier feature checkout, before isolating
the changes on main. Dependencies and the PostgreSQL image were already available:

| Configuration | Full-suite wall time |
| --- | ---: |
| Original serial baseline | 256.18 s |
| Updated serial (`-n 0`) | 197.31 s |
| Updated two workers (`-n 2`) | 95.37 s |
| Updated four workers, three runs | 51.18 s / 50.88 s / 52.53 s |

The four-worker median is 51.18 seconds, about 80% below the original baseline.
All five final runs passed the same 633 tests as the baseline, with no failures
or skips; the 13 latency benchmarks remain excluded. The original baseline is
pytest's elapsed time; updated measurements also include Python process launch.
These results meet the speed target without replacing the HTTP transport.

The two-worker measurement is local, not a hosted CI benchmark. The Linux CPU
installation was checked using a frozen dry run with CI's exact uv version
(0.12.17), and its dependency export contains CPU-only PyTorch without CUDA or
Triton. Hosted CI timings and cache hits require a workflow run.
