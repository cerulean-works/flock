# 048: per-token value corrections before schema 12 merges

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-23 |
| Builds on | [045](045-token-sidecar.md) (per-token values, context values, greedy tokens), [046](046-perplexity-tokens.md) (perplexity per-token values) |
| Amends | 045 (a model without a key-value cache now fails with greedy tokens instead of writing none; a NaN position's entropy is NaN) |
| Keeps | every task hash and suite hash, every request and score, every metric, schema 12's fields, the files a run writes for every model that decodes |
| Result | the Python scoring API refuses the token flags `EvalConfig` refuses; a model that can't decode with a cache fails before scoring when greedy tokens are asked for; a NaN logit no longer reads as a certain prediction |

The decision numbers are provisional: `048.D1` to `048.D3` take the next free global numbers after every stage merged before it.

## 1. Goal

Three gaps in stage 045's code, found by review after it was built and before it merged. Each one let a run finish with values that were missing or wrong, without an error. Schema 12 is not merged yet, so they are corrected here with no `schema_version` bump.

**Out of scope:** any change to a score, a task hash or a written field; decoding for models without a key-value cache.

---

## 2. Design decisions

### 048.D1. `score_task` and `score_prepared` refuse `context_tokens` or `greedy_tokens` without `tokens`

`EvalConfig` already refused them, but both functions default `tokens` to off for training loops, so `context_tokens=True` alone wrote nothing and a loop could run every checkpoint before anyone saw the values were missing. `score_prepared`, which `score_task` calls, now raises the same way before it scores.

### 048.D2. A model without a key-value cache fails before scoring when greedy tokens are asked for

045 wrote no greedy rows for a Flock model, silently, and a transformers model whose cache is not `past_key_values` (Mamba's `cache_params`) failed after its first decoding step, once the whole task was scored. `ScoringModel.decodes` is true only for a transformers model whose forward takes `past_key_values`. With `greedy_tokens`, `score_prepared` raises before scoring a model for which it is false, naming its class, as the pinned attention kernel fails loudly rather than run another one; `score_contexts` raises the same way when asked for tokens to decode. A Flock model still reads its contexts' values, with `num_tokens` 0.

### 048.D3. Only probability-0 terms are left out of entropy and its deviation

045 kept the terms with `probs > 0`, so a NaN logit, which makes every probability at its position NaN, gave entropy and deviation 0: a certain prediction. Now only terms whose probability is exactly 0 are masked (they would be `0 * -inf`), so a NaN position reads NaN in the sidecar. A finite position's values are bit for bit the same, since `exp` is never negative.

---

## 3. How it was actually built

- Tests: both functions refuse each flag before writing anything; a tiny Mamba model raises before its first batch is scored; a Flock model raises with greedy tokens and still reads contexts; a position with a masked token matches a reference over the unmasked ones, and a NaN position gives NaN entropy and deviation.
- The per-token buffer stays on the device for the whole task. Measured on OLMo 2 1B's tokenizer, the largest are DROP's context values (13.9M tokens, 334 MB) and FineWeb Simplified Chinese's perplexity values (8.3M tokens, 198 MB), far inside an H200's memory, so completed batches are not staged on the host.
- Base Easy on OLMo 2 1B, against a run made before these changes and before stage 047: equal task hashes, metrics and suites, and equal per-token values in all 109 sidecars. Prediction rows differ only by the `subject` and `meta` keys stage 047 added, and the other 71 tasks' files are byte-equal.
