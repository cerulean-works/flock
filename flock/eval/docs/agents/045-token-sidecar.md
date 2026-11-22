# 045: per-token values beside the predictions

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-23 |
| Builds on | [041](041-result-files.md) (041.D2 rows), [042](042-private-predictions.md) (042.D1 `private/`), [043](043-item-text.md) (043.D3 `pick` from the scorer), [044](044-row-sources.md) (044.D4 the tokenizer in the provenance) |
| Amends | 041.D2 (a run also writes `predictions/<task>.tokens.parquet` per multiple-choice task), 042.D1 (the file follows its predictions' privacy) |
| Keeps | every task hash, request, score and metric, every prediction row byte for byte, the training path's cost when it doesn't ask |
| Result | `ScoringModel.loglikelihood(..., tokens=True)` returns each continuation's per-token values; `ScoringModel.score_contexts` decodes 32 greedy tokens after each distinct context and reads the context's values, each by flag; `write_tokens` writes them as Parquet; `WrittenResults.tokens` reads them on demand |

The decision numbers are provisional: `045.D1` to `045.D5` take the next free global numbers after every stage merged before it.

## 1. Goal

Summed log-probabilities support item response theory, paired tests and choice-level calibration, but not what needs single tokens: where two checkpoints part along a choice, which tokens surprise a model, Min-K% (Shi et al. 2023, 2310.16789) and Min-K%++ (Zhang et al. 2024, 2404.02936) contamination checks, or a first-divergence view of what each model would write. This stage stores those values beside the predictions, cheaply enough to be on by default, without moving a score.

**Out of scope:** top-k alternatives, hidden states, logit or tuned lens, attention summaries (no evidence they help at this scale), generation tasks, perplexity tasks (stage 046), the dashboard's views.

---

## 2. Design decisions

### 045.D1. A Parquet file per task beside a thin JSON lines file

Per-token values are long numeric arrays: as JSON they would multiply the predictions' size and every reader's parse time. They go to `predictions/<task>.tokens.parquet`, one row per token sequence with list columns, zstd-compressed, as LightEval writes its details as Parquet. The JSON lines file is unchanged, so `diff`, `items` and the app read rows as before, and a run without the file still reads. One row per sequence keeps one doc's tokens together, so a reader filters by `id`.

### 045.D2. Five values per token, all from the distribution scoring already computes

For each scored token: its log-probability, its rank (1 plus the count of strictly more likely tokens), and at its position the entropy, the top-1 log-probability and the standard deviation of the log-probability under the distribution. Min-K%++ normalizes a token's log-probability by the mean and standard deviation of log p under p at its position (its eq. 3); the mean is minus the entropy, so the file stores the entropy and the deviation. They are computed in `_target_logprobs`' FP32 chunks, which are the same chunks with or without values, so no reduction changes order and no score moves. On by default: they read only the positions scoring keeps.

### 045.D3. Context values and greedy tokens come from a pass of their own, each by flag

Keeping every position's logits in the scoring pass would give context values for free, but a test showed it moves scores in the last bit: the LM head then runs at another shape. `score_contexts` instead reads each doc's distinct contexts once (`MCRequests.context_requests`: one per doc, one per choice where each has its own), decodes 32 tokens greedily with the key-value cache and no stop, and with `--context-tokens` keeps that first pass's logits at every position to read each context token's values. Thirty-two tokens follow the research answer's range of 16 to 32 for first-divergence views. A context loses tokens from the left to leave room for the decoded ones, as scoring cuts a long one. Both are off by default (`--greedy-tokens`, `--context-tokens`): measured on OLMo 2 1B over all of MMLU, HellaSwag and ARC-Easy, the greedy tokens took scoring from 354.7 s to 539.4 s even in batches 8 times scoring's size, against a budget of 1.3 times for the default; the choices' values alone stay on.

### 045.D4. Token ids are text: the file follows its predictions' privacy

A private dataset's file is created owner-only and uploaded under `private/`, as its predictions are (042.D1). The tokenizer's name and hash in the provenance (044.D4) say which vocabulary the ids belong to.

### 045.D5. The training path pays nothing unless it asks

`EvalConfig.tokens` defaults on and `context_tokens` off; `score_task` and `score_prepared` default both off, so a training loop scoring between steps writes and computes no values unless it passes `tokens=True`.

---

## 3. Plan

1. Values in `_target_logprobs` and flat device buffers in `_logprobs`; `loglikelihood(tokens=...)`.
2. `score_contexts`: greedy decoding and context values.
3. `write_tokens`, `tokens_name`, `WrittenResults.tokens` with a bytes reader for folders and tracker runs; uploads beside the predictions.
4. Flags in `EvalConfig` and `flock eval run`; tests against unbatched forward passes and uncached decoding; docs.

---

## 4. How it was actually built

- Values land in flat arrays on the device, one sequence after another, through an index built on the host from token counts; positions that hold no wanted token write to one spare slot. No batch waits for the GPU.
- The first version read context values from the scoring pass with every position's logits kept. A CPU test comparing predictions with and without them failed in the last bits of `logprob`, so context values moved to `score_contexts` (045.D3). The scoring pass now runs exactly as without values, which the tests check byte for byte on the predictions file.
- Greedy decoding is a loop over the model's forward pass with its key-value cache, not `generate`, so no generation config of the model (sampling, an end-of-sequence stop) applies. A Flock-registry model (`FlockCausalLM`) has no cache: it writes no greedy rows and reads context values in one pass.
- A model whose `max_length` is 32 or less writes `max_length - 1` greedy tokens.
- Greedy decoding was first on by default. Its 32 one-token steps per batch doubled scoring time (356.5 s to 710.7 s on the set above). Decoding in batches 8 times `batch_tokens`, which fits because the first pass keeps one position's logits, brought it to 1.52 times, still above the default's budget, so the tokens moved behind `--greedy-tokens`.
- Reading a run's files as bytes became the base reader for folders and tracker runs (`read_run_bytes`), with text read as its UTF-8 decoding.

## 5. Known gaps

- Unconditional (PMI) requests have values computed but not written.
- Greedy decoding batches by length like scoring, so a token at a near tie can differ between batch sizes in BF16.
- A context's values come from a forward pass apart from scoring's, in other batches, so they can differ from what scoring saw at the same positions by BF16 rounding.
