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

A task is changed when its `task_hash` or `limit` differs. A suite is changed when its `metric` or `suite_hash` differs, or when it holds a changed task or suite at any depth. `compare` lists the changed names in `Comparison.changed`, each with the first field that differs (`members` for a suite changed through a member), leaves them out of `tasks` and `suites`, and compares everything else as before. The CLI prints them on one line after the one-sided names, as `lambada (task_hash)`, so a `--limit` run against a full bank says why every name is listed and exits 2 when any name changed, 1 when a compared value breached, and 0 otherwise. So a changed name still fails a check the way the refusal did, but the output now shows whether anything else moved.

The task-hash and `limit` refusals of 013.D2 and the suite refusals of 018.D1 become this listing too. One rule for every per-name change is simpler than a listing for suites and a refusal for tasks, and a refresh needs it: a task whose definition changed (015's LAMBADA) changes every suite that holds it, and both should be named in one output. `compare` still refuses, with exit 2 and no comparison, when a run-level setting differs (`model`, `model_digest`, `dtype`, `batch_tokens`, `max_length`, `attention`), when no task is in both files, or when a file is broken. Those make every score another quantity, so nothing would be left to compare.

The "any depth" rule makes old and new files agree. A suite's hash covers its members' hashes all the way down (014.D7), so between two files with hashes, a changed member already changes every suite above it. A file before schema 5 has no suite hash, and the member rule gives the same names. Against such a file, a suite whose member list changed but whose members did not, shows a `members` breach, as before.

lm-evaluation-harness and OLMES have no suite hashes and no comparison command, so there is no precedent. The rule is argued from the refresh workflow.

### 021.D2. The bank test checks that the bank loads under the current layout and compares equal to its rewrite, not that its version matches

The test builds every `Results` field from the bank, writes the rewrite, and requires `compare` of the bank against it to have no breach and no changed name, with at most `schema_version` among the printed causes. A layout change that the bank can't load under, or that `compare` reads differently, still fails here, offline. A plain version bump doesn't, so it costs no GPU job. A bank is refreshed when its scores are meant to change, and the version difference is printed as a cause, as before 018.

Two schema notes, for readers of old files. Schema 6 gained `provenance.model_digest` after its first files were written, without a bump. So a file at 6 may lack the key, and `compare` reads it as `null`, a Hugging Face model's value. 019's `loss_per_token` and `perplexity` are metric keys, which tasks define, so they did not bump the schema either. The next change to a field bumps the version to 7.

### 021.D3. A Paloma FP32 bank, scored at a commit with 019

`tests/eval/regression/olmo-2-0425-1b-paloma-float32.json` holds all 309 Paloma tasks and their 10 suites, scored on `allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf` in FP32 at `53f4fb6`. It is accepted only if, against the previous FP32 Paloma run (stage 008's, at `f8f928e`), every doc's prediction row is the same, so the model's numbers are unchanged and only the two token metrics are new. It gets the same CI test as the `base_easy` bank, and `compare-regression.md` gives its command.

### 021.D4. The test suite caps its own thread pools at import

`tests/conftest.py` sets `OMP_NUM_THREADS=4`, `RAYON_NUM_THREADS=4` and `TOKENIZERS_PARALLELISM=false` with `os.environ.setdefault` at import. That runs before any test module imports torch or a tokenizer, so every pytest process and xdist worker is capped. PyTorch's and OpenMP's pools are sized from the core count by default, and the Rust tokenizers' Rayon pool too. So on a 216-core machine, one test process holds dozens of threads, and six of them hold hundreds. On a shared login node that limits a user's tasks (`RLIMIT_NPROC` counts threads), this crashed other processes. A value already set in the shell wins. `tests/eval/conftest.py`'s `one_thread` fixture stays: it sets torch's intra-op threads to one around tiny-model tests, which is faster, not just smaller. CI's `-n 6` is unchanged.

---

## 3. Plan

1. `compare.py`: `Comparison.changed`, the rule of 021.D1, and the refusals reduced to the run-level ones. Tests for the listing, the depth rule and the three exit codes.
2. The bank test of 021.D2, for both banks.
3. The Paloma bank of 021.D3: one GPU job, its prediction rows checked against stage 008's and its file copied unchanged.
4. `tests/conftest.py` and one sentence in `tests/README.md`.
5. Docs: `comparison.md` and `compare-regression.md`.

---

## 4. How it was actually built

- **`compare`.** `Comparison.changed` holds the changed tasks, then the changed suites in the bank's order, each with its field. A suite's members are read from both files: a changed task that joined a suite changes the suite, as its hash would. A member is a task when the bank has a task of that name, else a suite, as `_suite_tolerance` reads it. A suite holding a changed member is listed with `members` even when its hash differs too, since the hash covers the member's, so a parent suite is not mistaken for a redefined one. Each file writes a suite after the suites it holds, but a suite may hold, in the new file only, a suite that comes after it in the bank, so the member rule repeats until no suite is added. A test builds that case.
- **The old bank against the current one.** `flock eval compare` of the schema 4 bank of stage 013 against the schema 6 bank prints `changed, not compared: lambada (task_hash), base_easy:qa_rc (members), base_easy:qa_bpb (members), base_easy (members)` and `compared: tasks 108, suites 7, breaches 0`, and exits 2. Stage 018 needed a hand-trimmed copy of the old bank for the same check. `base_easy` is listed because it holds the two changed suites, which is what its hash would say.
- **The Paloma bank.** All 77,746 prediction rows (id, log probability, tokens, bytes, windows) of the 309 tasks equal stage 008's run. The task hashes differ from that file, all 309, because `Dataset` gained `row_index` after it, and they equal the hashes on `main` before this stage. 67 of the 309 `bits_per_byte` values differ from stage 008's in the last bit or two (relative difference at most 2.8e-16). Stage 019 made the byte count a CPU tensor, and PyTorch divides a CUDA tensor by a CPU scalar by multiplying by its reciprocal. One doc sum reproduces both values for every task. The file is what `main` writes today, so it is the bank, and a later run on `main` should equal it bit for bit. Scoring took 4 h 45 min on one H200 instead of the expected one hour: the GPU nodes were power-throttled that day (a WikiText-103 probe took 135 s against 20.4 s in stage 019's job, with the same `bits_per_byte`).
- **Thread caps.** The peak thread count of the pytest process for `pytest -q tests/eval -n 0`, sampled every 0.1 s with no thread variable in the shell on a 216-core node, fell from 69 to 5.
