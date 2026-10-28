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

## Reliability on isolated main, 2026-10-29

The test changes were ported to a separate worktree based on main at `e217433`
(including PR #49). Fetching main confirmed that base was current. The worktree
has its own `.venv`, CLI executable and editable package; no hub or training work
from the feature checkout is included. Production source is identical to main.

The initial ten-run batch found one failure: simulating one second produced
`0.9999997615814209` seconds after PostgreSQL rounded a stored heartbeat timestamp.
The billing assertion now checks one simulated second with a one-microsecond
absolute tolerance. A controlled fractional timestamp reproduced the failure
before the fix and passed 20 consecutive checks afterward.

A fresh ten-run batch on test commit `fd67953` passed **all 6,490 test executions**:

| Workers | Full-suite runs | Median wall time | Range |
| --- | ---: | ---: | ---: |
| 4 (local default) | 8 | 54.81 s | 54.08–57.73 s |
| 2 (CI configuration) | 2 | 102.67 s | 102.44–102.90 s |

Each run used a fresh pytest process and throwaway services. All 649 selected test
identities matched exactly, with no failures, errors, skips or retries. The 13
latency benchmarks remained excluded, and tracked source stayed unchanged for
the entire batch. These are local macOS ARM64 measurements with dependencies
and the PostgreSQL image already available.

Reports are retained locally under the ignored directories
`test-results/reliability-2026-10-29/` (initial batch and failure evidence) and
`test-results/reliability-2026-10-29-fixed/` (final batch). Each contains a manifest,
per-run logs, JUnit XML and summary JSON; the final batch also has the controlled
precision regression log.

CI receives the parallelism and waiting improvements when these changes are
merged. It uses two workers, so the local four-worker runtime is not a hosted CI
promise. The existing [PR #49 CI job](https://github.com/cerulean-works/flock/actions/runs/40737159565/job/122988684283)
reported 649 passed in 292.73 seconds. The updated workflow's hosted runtime
remains unmeasured. Its CPU-only Linux dependency export was checked to contain
PyTorch `2.14.1+cpu` with no CUDA or Triton dependencies; the default GPU dependency
configuration is preserved.

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
