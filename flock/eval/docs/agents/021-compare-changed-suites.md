# 021: `compare` lists what changed and compares the rest; the banks need not match the schema

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-02 |
| Builds on | [013](013-score-compare.md) (D2: refusals, D4: the bank), [014](014-run-provenance.md) (D7: `suite_hash`), [018](018-bank-schema6.md) (D1: the suite hash refusal, the bank test), [019](019-fineweb-edu-dev.md) (the token metrics of every perplexity task) |
| Keeps | every tolerance, every run-level refusal, every task and suite hash, and every score in both banks |
| Result | `compare` lists a task or suite scored differently and compares everything else; the bank test no longer pins `schema_version`; a Paloma FP32 bank beside the `base_easy` one; the test suite caps its own threads |

## 1. Goal

Since 018.D1, adding one task to `base_easy` makes `compare` refuse the whole comparison against the bank, so every other task's breaches stay hidden until someone trims a copy of the bank by hand, which 018 itself had to do. And 018's bank test pins the bank to the current `schema_version`, so any change to the layout, in anyone's PR, also costs a three-hour GPU job to refresh the bank in that PR. This stage keeps both checks but makes them cheap:

1. **Changed names are listed.** A task or suite scored differently is named and skipped, and every other task and suite is compared and printed.
2. **The bank test asks the bank to load.** The bank must still load under the current layout and compare equal to its own rewrite. Its version may lag.
3. **A Paloma bank.** 019 added `loss_per_token` and `perplexity` to every perplexity task without a schema bump, so a new Paloma run against the last FP32 Paloma file shows 618 one-sided metrics. A re-score at a commit with 019 becomes the Paloma bank.
4. **Thread caps for the tests.** The test suite sets its own thread pools, so a run on a shared many-core machine stays small.

**Out of scope:** refreshing the `base_easy` bank, a BF16 bank, tolerance constants, and the next schema version.

---

## 2. Design decisions

### 021.D1. A task or suite in both files that was scored differently is listed as changed and skipped; the exit code stays 2

A task is changed when its `task_hash` or `limit` differs. A suite is changed when its `metric` or `suite_hash` differs, or when it holds a changed task or suite at any depth. `compare` lists the changed names in `Comparison.changed`, leaves them out of `tasks` and `suites`, and compares everything else as before. The CLI prints them on one line after the one-sided names and exits 2 when any name changed, 1 when a compared value breached, and 0 otherwise. So a changed name still fails a check the way the refusal did, but the output now shows whether anything else moved.

The task-hash and `limit` refusals of 013.D2 and the suite refusals of 018.D1 become this listing too. One rule for every per-name change is simpler than a listing for suites and a refusal for tasks, and a refresh needs it: a task whose definition changed (015's LAMBADA) changes every suite that holds it, and both should be named in one output. `compare` still refuses, with exit 2 and no comparison, when a run-level setting differs (`model`, `model_digest`, `dtype`, `batch_tokens`, `max_length`, `attention`), when no task is in both files, or when a file is broken. Those make every score another quantity, so nothing would be left to compare.

The "any depth" rule makes old and new files agree. A suite's hash covers its members' hashes all the way down (014.D7), so between two files with hashes, a changed member already changes every suite above it. A file before schema 5 has no suite hash, and the member rule gives the same names. Against such a file, a suite whose member list changed but whose members did not, shows a `members` breach, as before.

lm-evaluation-harness and OLMES have no suite hashes and no comparison command, so there is no precedent. The rule is argued from the refresh workflow.

### 021.D2. The bank test checks that the bank loads under the current layout and compares equal to its rewrite, not that its version matches

The test builds every `Results` field from the bank, writes the rewrite, and requires `compare` of the bank against it to have no breach and no changed name, with at most `schema_version` among the printed causes. A layout change that the bank can't load under, or that `compare` reads differently, still fails here, offline. A plain version bump doesn't, so it costs no GPU job. The banks are refreshed on the eval loop's schedule, and the version difference is printed as a cause, as before 018.

Two schema notes, for readers of old files. Schema 6 gained `provenance.model_digest` after its first files were written, without a bump. So a file at 6 may lack the key, and `compare` reads it as `null`, a Hugging Face model's value. 019's `loss_per_token` and `perplexity` are metric keys, which tasks define, so they did not bump the schema either. The next change to a field bumps the version to 7.

### 021.D3. A Paloma FP32 bank, scored at a commit with 019

`tests/eval/regression/olmo-2-0425-1b-paloma-float32.json` holds all 309 Paloma tasks and their 10 suites, scored on `allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf` in FP32 at `53f4fb6`. It is accepted only if, against the previous FP32 Paloma run (stage 008's, at `f8f928e`), every task hash is equal and every `bits_per_byte` is byte for byte the same, so that only the two token metrics are new. It gets the same CI test as the `base_easy` bank, and `compare-regression.md` gives its command.

### 021.D4. The test suite caps its own thread pools at import

`tests/conftest.py` sets `OMP_NUM_THREADS=4`, `RAYON_NUM_THREADS=4` and `TOKENIZERS_PARALLELISM=false` with `os.environ.setdefault` at import. That runs before any test module imports torch or a tokenizer, so every pytest process and xdist worker is capped. PyTorch's and OpenMP's pools are sized from the core count by default, and the Rust tokenizers' Rayon pool too. So on a 216-core machine, one test process holds dozens of threads, and six of them hold hundreds. On a shared login node that limits a user's tasks (`RLIMIT_NPROC` counts threads), this crashed other processes. A value already set in the shell wins. `tests/eval/conftest.py`'s `one_thread` fixture stays: it sets torch's intra-op threads to one around tiny-model tests, which is faster, not just smaller. CI's `-n 6` is unchanged.

---

## 3. Plan

1. `compare.py`: `Comparison.changed`, the rule of 021.D1, and the refusals reduced to the run-level ones. Tests for the listing, the depth rule and the three exit codes.
2. The bank test of 021.D2, for both banks.
3. The Paloma bank of 021.D3: one GPU job, its file checked against stage 008's and copied unchanged.
4. `tests/conftest.py` and one sentence in `tests/README.md`.
5. Docs: `comparison.md` and `compare-regression.md`.

---

## 4. How it was actually built
