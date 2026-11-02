# 018: the FP32 bank at schema 6, and the suite hash in `compare`

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-01 |
| Builds on | [013](013-score-compare.md) (D2: refusals, D4: the bank and its refresh), [014](014-run-provenance.md) (D5: the library versions, D7: `suite_hash`), [015](015-greedy-match.md) (D3: LAMBADA's new hash, D4: schema 6) |
| Keeps | every task definition and tolerance; the bank's scores move only where 015 moved them (LAMBADA, `base_easy:qa_rc`) |
| Result | `compare` refuses a common suite with another `suite_hash`; the bank `tests/eval/regression/olmo-2-0425-1b-float32.json` rewritten by the same command at schema 6 |

The file number and the decision numbers are provisional. The file takes the next free number when its PR is opened.

## 1. Goal

013.D4 planned that the bank's first refresh after the run-provenance stage would bring each task's `dataset`, each suite's `suite_hash` and the `tokenizers` and `datasets` versions, and that `compare` would then check the suite hash. The bank is still the schema 4 file of stage 013, its CI test fills the missing fields with stand-ins, and `compare` refuses it against any new `base_easy` run because LAMBADA's hash moved (015.D3). This stage makes the refusal and rewrites the bank.

**Out of scope:** a BF16 bank, other models, and any change to the tolerances.

---

## 2. Design decisions

### 018.D1. `compare` refuses a common suite whose `suite_hash` differs, and skips the check for a file before schema 5

A suite's hash covers its name, `metric`, whether it is scored, and each member's name and hash, all the way down (014.D7). Two equal suite hashes mean the same members scored the same requests; two different ones mean the suite's score is another quantity. So a difference refuses, before any score is read, the way a task's `task_hash` does (013.D2).

A file written before schema 5 has no `suite_hash`. When either file's `schema_version` is below 5, the check is skipped and the suites are compared on `members` and `score` as before, so the old bank can still be compared with the new one in the refresh PR (section 4). The skip is tied to the version, not to the key: a file of schema 5 or later without a `suite_hash` is broken and refuses like any missing key (013.D2).

`members` stays compared. Between two files that both carry hashes it can only differ when the hash does, so in practice it now speaks for older files only.

---

## 3. Correction to 013

013.D4 says `compare` will check the suite hash "in place of `members`"; both are checked (018.D1). 013's first known gap says that until the bank is refreshed, a task added to `base_easy` shows as a breach on the suites that hold it. Against a bank with suite hashes it is a refusal: the new member changes each holding suite's hash. The fix is the same, a refresh.

015.D4 says `compare` reads the version only to print it. It now also reads it to decide whether the suite hash is checked (018.D1).

The results guide said that files written before schema 6 have no `model_digest`. Schema 6 files written before the model registry landed, this bank among them, have none either; they read as `null` all the same. The guide now says so.

---

## 4. Plan

1. `compare.py`: the refusal of 018.D1, its test, and the refusals list in `comparison.md`.
2. The bank: the command of 013.D4 at this branch's first commit as one GPU job, its `results.json` copied unchanged over the old bank.
3. `compare` of the old bank against the new one, twice: as is, expected to refuse on `lambada` (015.D3); and against a copy of the old bank without `lambada`, `base_easy:qa_rc` and `base_easy:qa_bpb`, expected to have no breach. LAMBADA's `bpb` must equal its old banked value exactly, since its requests did not change.
4. The bank's CI test reads the bank as written, without stand-ins for missing fields.

---

## 5. How it was actually built

- **Code.** `b75c96a` adds the refusal, its test and the `comparison.md` lines. Review then tied the skip to `schema_version` below 5 rather than to a missing key, so a schema 6 file without a `suite_hash` refuses (`d9faed9`, tested). The test gives the old file schema 4 and the new one another suite hash, so the skip is exercised.
- **The bank.** `flock eval run --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf --suite base_easy --dtype float32 --out <dir>` at `b75c96a` (scoring code equal to `main` at `cd0de44`), one H200, 2 h 59 min, written at schema 6 with `tokenizers` 0.23.3 and `datasets` 5.1.0. Its provenance records the command as `flock-eval eval run ...`, the script's old name. The file has no `model_digest`: the model registry, which adds it, landed after the job, and a Hugging Face model's digest reads as `null` either way. 109 tasks and 10 suites, as before. Its `results.json` replaces the bank unchanged.
- **Old bank against new, as is.** Refused, exit 2, on `lambada`'s `task_hash` (`2fbcb961` to `147d9b4c`), as 015.D3 predicts.
- **Old bank without `lambada`, `base_easy:qa_rc` and `base_easy:qa_bpb`, against new.** `compared: tasks 108, suites 8, breaches 0`. `base_easy:qa_bpb` has to go too: it holds `lambada`, and a suite's tolerance needs every member in the file. Its score was checked by hand instead.
- **Equality.** Every metric, `num_docs` and truncation count of the 108 other tasks is byte-equal. LAMBADA's `bpb` is byte-equal (0.4381837546825409); it gained `acc_greedy` (0.6019794344902039) as its primary metric. Every suite score is byte-equal except `base_easy:qa_rc`, which gained `lambada` as a member (0.5967264635396581 to 0.5969652349465011).
- **CI test.** The bank test now builds every field straight from the file and checks that `compare` sees no provenance difference and that the file's `schema_version` is the one `Results` writes.

---

## 6. Known gaps

- The tolerances of 013.D1 are still argued, not measured: this refresh ran on the same GPU type and libraries as the old bank, so it cannot measure drift across stacks.
