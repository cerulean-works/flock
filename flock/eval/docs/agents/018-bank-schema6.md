# 018: the FP32 bank at schema 6, and the suite hash in `compare`

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-01 |
| Builds on | [013](013-score-compare.md) (D2: refusals, D4: the bank and its refresh), [014](014-run-provenance.md) (D1: `suite_hash`, the library versions), [015](015-greedy-match.md) (D3: LAMBADA's new hash, D4: schema 6) |
| Keeps | every score, request, task hash and tolerance |
| Result | `compare` refuses a common suite with another `suite_hash`; the bank `tests/eval/regression/olmo-2-0425-1b-float32.json` rewritten by the same command at schema 6 |

The file number and the decision numbers are provisional. The file takes the next free number when its PR is opened.

## 1. Goal

013.D4 planned that the bank's first refresh after the run-provenance stage would bring each task's `dataset`, each suite's `suite_hash` and the `tokenizers` and `datasets` versions, and that `compare` would then check the suite hash. The bank is still the schema 4 file of stage 013, its CI test fills the missing fields with stand-ins, and `compare` refuses it against any new `base_easy` run because LAMBADA's hash moved (015.D3). This stage makes the refusal and rewrites the bank.

**Out of scope:** a BF16 bank, other models, and any change to the tolerances.

---

## 2. Design decisions

### 018.D1. `compare` refuses a common suite whose `suite_hash` differs, and skips the check for a file before schema 5

A suite's hash covers its name, `metric`, whether it is scored, and each member's name and hash, all the way down (014.D1). Two equal suite hashes mean the same members scored the same requests; two different ones mean the suite's score is another quantity. So a difference refuses, before any score is read, the way a task's `task_hash` does (013.D2).

A file written before schema 5 has no `suite_hash`. When either file's `schema_version` is below 5, the check is skipped and the suites are compared on `members` and `score` as before, so the old bank can still be compared with the new one in the refresh PR (section 4). The skip is tied to the version, not to the key: a file of schema 5 or later without a `suite_hash` is broken and refuses like any missing key (013.D2).

`members` stays compared. Between two files that both carry hashes it can only differ when the hash does, so in practice it now speaks for older files only.

---

## 3. Correction to 013

013.D4 says `compare` will check the suite hash "in place of `members`"; both are checked (018.D1). 013's first known gap says that until the bank is refreshed, a task added to `base_easy` shows as a breach on the suites that hold it. Against a bank with suite hashes it is a refusal: the new member changes each holding suite's hash. The fix is the same, a refresh.

---

## 4. Plan

1. `compare.py`: the refusal of 018.D1, its test, and the refusals list in `comparison.md`.
2. The bank: the command of 013.D4 at this branch's first commit as one GPU job, its `results.json` copied unchanged over the old bank.
3. `compare` of the old bank against the new one, twice: as is, expected to refuse on `lambada` (015.D3); and against a copy of the old bank without `lambada` and `base_easy:qa_rc`, expected to have no breach. LAMBADA's `bpb` must equal its old banked value exactly, since its requests did not change.
4. The bank's CI test reads the bank as written, without stand-ins for missing fields.

---

## 5. How it was actually built

RESULTS

---

## 6. Known gaps

- The tolerances of 013.D1 are still argued, not measured: this refresh ran on the same GPU type and libraries as the old bank, so it cannot measure drift across stacks.
