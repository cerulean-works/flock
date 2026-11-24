# 049: remove the three optional per-token outputs

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-24 |
| Builds on | [045](045-token-sidecar.md) (045.D1 the Parquet file, 045.D2 the values), [046](046-perplexity-tokens.md) |
| Amends | 045.D3 (withdrawn: no context values or greedy tokens), 046.D1 and 046.D2 (withdrawn: a perplexity task writes no per-token file), 045.D1 (a file holds `continuation` rows only) |
| Keeps | every task hash, request, score and metric, every prediction row byte for byte, the default per-token file's columns and values, `schema_version` 12, and the readers of files already written |
| Result | `--context-tokens`, `--greedy-tokens` and `--perplexity-tokens` are gone, with `ScoringModel.score_contexts`, `ScoringModel.byte_ends`, `rolling_loglikelihood`'s `tokens`, `ContextScores`, `MCRequests.context_requests` and `write_perplexity_tokens` |

The decision numbers are provisional: `049.D1` to `049.D3` take the next free global numbers after every stage merged before it.

## 1. Goal

Stages 045 and 046 added three outputs behind flags: the per-token values of each doc's context, the first 32 tokens greedy decoding writes after it, and the per-token loss of perplexity tasks. No code reads them: the app and the item reader read only `continuation` rows, and the one consumer in progress, a token view, reads the same. They cost the most of anything in the per-token file (with every flag a measured run took 2.06 times as long as without per-token values, and decoding alone 1.52 times on MMLU, HellaSwag and ARC-Easy), and the code that wrote them was a large share of `model.py` and `results.py`. This stage removes them and keeps the default file as it is.

**Out of scope:** the default per-token file and its values, `ScoringModel.encode`, any other cleanup of the scoring path.

---

## 2. Design decisions

### 049.D1. The schema stays 12

The flags never wrote a field into `results.json` (only the recorded `command` shows them), and no prediction row changes. The file keeps its columns; it no longer has `context` or `greedy` rows, and a perplexity task no longer has a file. A version bump marks a change of fields, so there is none, and a reader of schema 12 reads files from before and after this stage alike.

### 049.D2. Files written with the flags stay readable as written

Runs written with the flags before this stage are still schema 12. No reader code was specific to them: `WrittenResults.tokens` returns every row of a task's file, `context` and `greedy` rows included, and a perplexity task's file (one `text` row per doc with `byte_end` and no token ids) as its rows; the tracker upload sends the file whole; `diff` and `items` never read per-token files. So nothing is kept to read them, and the results page says in one line that such files exist.

### 049.D3. What only the removed paths used goes with them

`_decode` and `_left_padded` served decoding and the context pass alone. `_batches` loses its `budget` argument, which only decoding passed (it batched by 8 times `batch_tokens`), and `_TokenBuffer.write` loses `ends`, since every sequence it now writes ends at its batch's last column. A leftover `predictions/<task>.tokens.parquet` beside a perplexity task is still refused, as before, so no reader pairs another run's file with this run's predictions.

---

## 3. Plan

1. Remove the three options from the CLI, `EvalConfig`, `score_task` and `score_prepared`.
2. Remove the code only they reach in `model.py`, `requests.py`, `results.py`, `run.py` and `tasks/base.py`.
3. Remove their tests and docs; keep the tests of the default file.
4. Prove the default path unchanged: rescore one banked model and compare predictions byte for byte and every per-token value.

---

## 4. How it was actually built

- `write_tokens` builds one table of `continuation` rows, the same rows and schema as before.
- `rolling_loglikelihood` and `_score_perplexity` are back to their form before stage 046.
- The task snapshot is unchanged and was not regenerated.

## 5. Correction to earlier stage files

Two merged stage files carry the number 043, `043-item-text.md` and `043-tc-edu-manyshot.md`, and both number their decisions from 043.D1, so a bare "043.D3" names two decisions. Merged stage files are not edited, so the correction is recorded here: every citation of 043 in the later stage files 044, 045 and 046 means `043-item-text.md`, and the many-shot file's decisions are cited as `043-tc-edu-manyshot` D1 to Dn from now on.
