# 019: the FineWeb-Edu dev set, scored as loss and perplexity per token

| | |
| --- | --- |
| Status | done |
| Date | 2026-11-01 |
| Builds on | [008](008-paloma.md) (`PerplexityTask`, disjoint windows, `bits_per_byte` as a ratio of sums), [012](012-task-snapshot.md) (the snapshot), [014](014-run-provenance.md) (the results file) |
| Keeps | every request, every task hash (Paloma's 309 included) and every multiple-choice result; `bits_per_byte` byte for byte |
| Result | `PerplexityTask` reports `loss_per_token` and `perplexity`, equal to a plain FP32 forward pass within 1e-7 relative on `wikitext_103`; `fineweb_edu` scores Qwen2.5-0.5B at perplexity 10.958 and Llama 3.2 1B at 8.971 in FP32 |

## 1. Goal

The cycle 3 lab ("predictable scaling") ranks pairs by the perplexity of their 1B model on a designated FineWeb-Edu evaluation set, at a context of 8192 tokens, scores forecasts in log-perplexity, and asks every pair to log `eval/loss` (token-averaged cross-entropy on that set) and `eval/perplexity` while training. It names no set and no evaluation code. This stage provides both, so the number on the leaderboard and the number in each pair's training log are the same definition:

1. **Token metrics.** Every `PerplexityTask` reports `loss_per_token` (nats) and `perplexity` beside `bits_per_byte`.
2. **A dev set.** 5,000 documents of FineWeb-Edu, sampled with a fixed seed from one named file, pinned as a public dataset whose ids training data drops.
3. **One task.** `fineweb_edu`: that dev set, whole documents, windows of 8192 tokens, `perplexity` as its primary metric.

**Out of scope:** any change to `flock.training`; a second window size; a suite holding `fineweb_edu`; fitting scaling laws; removing near-duplicates of the dev set from the training data (section 2.3).

---

## 2. Design decisions

### 019.D1. `loss_per_token` and `perplexity` are ratios of sums over the task's docs

`loss_per_token` is the FP64 sum of every predicted token's negative log-probability over all of the task's docs, divided by the number of predicted tokens. `perplexity` is `exp` of it. Both pool tokens across docs, as `bits_per_byte` pools bytes (008.D5): a long doc counts by its length, and the number is exactly the "token-averaged cross-entropy" a trainer computes over a batch. It is the quantity scaling laws are fitted to: Kaplan et al. (2020, arXiv 2001.08361, sec. 2) record the cross-entropy loss "on the WebText2 test distribution", and Hoffmann et al. (2022, arXiv 2203.15556, sec. 1, footnote 2) fit "the smoothed training loss which is an unbiased estimate of the test loss" while training sees each token once. A dev set drawn from the training distribution and held out of it makes the lab's number that same test loss. Perplexity per token is Paloma's primary metric (arXiv 2312.10523, sec. 3), and it compares models only when they share a tokenizer (App. C.2.2), as every pair of the lab does.

Every token of a doc is predicted exactly once by [`rolling_loglikelihood`](../../model.py) (008.D3), the first one from the prefix token, so the token count is the doc's token count, which `Scores.num_tokens` already carries. Nothing new is computed on the GPU: one more sum and one more host copy per task.

OLMES has the same metric, `ppl_token` in `PerplexityMetric` (`oe_eval/metrics/perplexity.py`), but its rolling requests count one token per window (`num_tokens` is `len(cont_toks[target_mask])` on a batch of one row), so on Paloma's `wikitext_103` it reports 170 tokens for 61 docs and a perplexity of `1e30`, its overflow value. Parity for the token metrics is therefore checked against a plain forward pass over the same windows, not OLMES (section 4).

### 019.D2. `primary_metric` is a field of `PerplexityTask`, outside the task hash

`PerplexityTask` gains `primary_metric`, defaulting to `bits_per_byte` so Paloma is unchanged, and validated against `reported_metrics` as `MultipleChoiceTask` validates its own. The task hash covers what decides the requests: name, dataset, domain and window (results file docs, "The task hash"). Which reported metric is read first changes no request, so `task_hash` leaves `primary_metric` out, and Paloma's 309 hashes stay what the bank and every earlier results file recorded. A test pins two Paloma hashes to their values on `main`.

### 019.D3. The dev set: 5,000 docs drawn uniformly from one named file of the newest dump

FineWeb-Edu (Penedo et al. 2024, arXiv 2406.17557) has no validation split, so the lab's set has no precedent to follow and is defined here:

- **Source.** `HuggingFaceFW/fineweb-edu` at revision `87f09149`, the `default` configuration's newest CommonCrawl dump (`CC-MAIN-2025-26`), file `000_00000.parquet` (435,357 rows). One file keeps the build to a single download that anyone can repeat.
- **Sample.** 5,000 rows drawn without replacement, uniformly over the file's rows, with a fixed seed. 5,000 is a document count, not a token target. It gives about 5 million tokens (5,546,839 by FineWeb-Edu's own GPT-2 `token_count`), far more than a trainer's per-batch loss averages over, and few enough to score on one GPU in minutes. The token sum is reported, not tuned.
- **Fields.** `id` and `text`, which `PerplexityTask` reads, plus `url` and `dump` for whoever audits the set.
- **Held out by id.** The training data must drop these ids. FineWeb-Edu is FineWeb filtered by an educational-quality classifier (sec. 5), and FineWeb runs MinHash deduplication on "each snapshot (independently from the others)" (sec. 3.4), so a dev document can have near-duplicates in other dumps that dropping ids does not remove. That is a known limitation, stated in the dataset card and the docs.

The set is the public `cerulean-works/fineweb-edu-dev`, split `validation`, pinned by revision in the task.

### 019.D4. One task, window 8192, in no suite

`fineweb_edu` is a `PerplexityTask` with `window=8192`, the lab's context, so each window is a full training sequence. It is not in `base_easy` or any other suite: it is the lab's instrument, scored on its own. A model whose `max_length` is below 8192 (OLMo 2 1B has 4096) can't run it: `evaluate` raises before scoring with the task's name, the window and the model's `max_length`.

---

## 3. Plan

1. `PerplexityTask`: `primary_metric` field and its check; `reported_metrics` lists the three metrics; `task_hash` leaves `primary_metric` out.
2. `run.py`'s perplexity path reports the three metrics.
3. `tasks/fineweb_edu.py` and its registration.
4. Tests: the two metrics against a hand computation, batch invariance, the hash pin, the primary-metric check, the registered task.
5. Snapshot regenerated: `fineweb_edu` is new, and every perplexity task gains its `primary_metric` field.
6. Docs: a page for the lab, plus `tasks.md`, `metrics.md`, `results.md` and `evaluate-checkpoint.md`.
7. Checks on real weights: the token metrics against a plain forward pass on `wikitext_103`, and the first `fineweb_edu` number.

---

## 4. How it was actually built

- **Metrics.** `_score_perplexity` sums the docs' FP64 log-probabilities once, divides by the summed bytes and by the summed tokens, and copies both quotients to the host in one `tolist()`, so the task still waits for the GPU once. `perplexity` is `math.exp` of the host value, or infinity where that overflows (above about 709.78 nats), so a diverged checkpoint doesn't lose the run. `bits_per_byte` is the same expression as before and reads back bit for bit.
- **Hash.** `task_hash` deletes `primary_metric` from the definition before hashing. On the tiny test model and on Paloma's real data, all 309 Paloma hashes are equal to `main`'s, and a test pins two of them on fixed docs.
- **Locks.** On a tiny Llama model, every multiple-choice request score, `results.json` and predictions file is byte-equal to `main`. A text task gives the same `bits_per_byte` (to the last bit), task hash and predictions, and only gains `loss_per_token` and `perplexity`.
- **Schema.** `schema_version` stays 6: metric keys are defined by each task and the file layout is unchanged, so the multiple-choice lock above is the acceptance. One consequence: `compare` of a new Paloma run against a Paloma results file written before this stage reports `loss_per_token` and `perplexity` as one-sided breaches (618 on the full Paloma run) and exits with a breach until that reference is re-scored.
- **Snapshot.** The diff is 309 `"primary_metric": "bits_per_byte"` lines and the new `fineweb_edu` entry. The toy requests are unchanged.
- **Dev set.** Built from the file named above with seed `20261101`: 5,000 docs, 27,077,101 UTF-8 bytes, 5,546,839 GPT-2 tokens. A second build is byte-identical.
- **Docs.** The docs page carries a plain PyTorch `document_loss` for pairs whose trainer computes the loss itself. On the tiny model it matches `rolling_loglikelihood` within 8e-6 nats at windows 1, 3, 8 and 64.
- **Checks on real weights** (one H200, FP32, `batch_tokens` 32768). On Paloma's `wikitext_103` with OLMo 2 1B (window 2048, 61 docs), a plain Transformers forward pass over the same windows gives the same 285,336 tokens and a perplexity 9.6e-8 relative from flock's (8.881759 against 8.881759, no doc more than 0.0021 nats apart); `bits_per_byte` is byte-equal to the result before this stage. On `fineweb_edu`, Qwen2.5-0.5B (`060db649`) scores perplexity 10.958, `loss_per_token` 2.3941 and `bits_per_byte` 0.6907 in 164 s; Llama 3.2 1B (`4e20de36`) scores 8.971, 2.1940 and 0.6254 in 343 s. OLMo 2 1B is refused before scoring with the message below.
- **Error path.** `evaluate` already checks every perplexity task's window against the model before scoring, naming the task: `fineweb_edu: window 8192 is above <model>'s max_length 4096`. `score_task` called directly from a training loop raises from `rolling_loglikelihood` without the task's name, which is enough for a caller that passed one task.
