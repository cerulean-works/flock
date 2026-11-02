# 022: prepare a task once, score it at every evaluation step

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-02 |
| Builds on | [001](001-eval-core.md) (`score_task`, `ScoringModel`), [008](008-paloma.md) (`PerplexityTask`, rolling windows), [019](019-fineweb-edu-dev.md) (`fineweb_edu` and the in-training snippet) |
| Keeps | every request, token id, score, task hash, `results.json` and predictions file; `score_task(model, task)` and `evaluate` as they are |
| Result | `prepare_task` loads and tokenizes a task once; `score_prepared` scores it with a model that has the same tokenizer |

## 1. Goal

A training run that logs `eval/loss` on `fineweb_edu` every N steps calls [`score_task`](../../run.py) each time. Each call reloads the split, rehashes 27 MB of text and retokenizes it, and builds every multiple-choice request string again. None of that depends on the weights. This stage splits `score_task` into a host-side preparation, done once, and the scoring, done at every step, so only the forward passes repeat.

**Out of scope:** an eval step inside `flock.training`; any change to which tokens are scored or how; a cache that prepares tasks behind the caller's back; health metrics.

---

## 2. Design decisions

### 022.D1. One function pair: `prepare_task` and `score_prepared`, and `score_task` is the two composed

`prepare_task(model, task, limit=None)` loads the docs and returns a prepared task: for a multiple-choice task its `MCRequests` (docs, requests, hash) and each request's (context, continuation) token ids, and for a perplexity task its docs, its task hash and each doc's token ids. `score_prepared(model, prepared, out=None)` scores it and returns the same `TaskResult` as before. `score_task(model, task, limit, out)` keeps its signature and is now `score_prepared(model, prepare_task(model, task, limit), out)`, so the prepared and the one-call paths are one code path and can't drift apart. `evaluate` and the CLI call `score_task` as before.

lm-evaluation-harness builds a task's instances once per run and reuses them across its request types (`build_all_requests` in `lm_eval/api/task.py`). No paper prescribes this: it is a performance change that must leave every score unchanged, and section 4 checks that byte for byte.

The two kinds of prepared task are two frozen dataclasses, `PreparedMultipleChoice` and `PreparedPerplexity`, joined in the alias `PreparedTask`, because they hold different things. `score_prepared` branches on the kind as `score_task` branched on the task's class.

### 022.D2. A prepared task is tied to one tokenizer object, checked by identity

The token ids are the tokenizer's. A model with another tokenizer would score other tokens without any error, so `score_prepared` raises `ValueError` unless `model.tokenizer is prepared.tokenizer`. Identity is strict: an equal tokenizer loaded twice is refused. That is the safe direction (a refusal costs one more `prepare_task`, a silent mismatch costs a wrong number), and it costs nothing per step. A training loop loads its tokenizer once at startup, prepares with it, and builds its `ScoringModel` with it. Comparing tokenizer contents instead would hash a vocabulary of up to several MB at every step, and there is no single field that names a tokenizer's version in memory.

The model's own limits stay checked at every call, since one prepared task may be scored by models with different `max_length`: a continuation longer than `max_length` and a window above it raise exactly as before.

### 022.D3. `ScoringModel` takes the token ids as an optional argument

`loglikelihood(requests, encoded=None)` and `rolling_loglikelihood(texts, window, token_ids=None)` skip tokenizing when given the ids, and tokenize as before when not. The requests and texts are still passed, because the errors name them. The two tokenizing steps become public as `encode_requests` (was `_encode`) and `tokenize` (was `_tokenize`), so `prepare_task` doesn't reach into private methods, and a (context, continuation) pair is a named tuple, `Encoded`, now that it crosses a public boundary. `encode(request)` is unchanged.

---

## 3. Plan

1. `model.py`: `Encoded`, `encode_requests`, `tokenize`, the two optional arguments.
2. `run.py`: the two prepared dataclasses, `prepare_task`, `score_prepared`, `score_task` composed from them.
3. Tests: prepared and one-call paths give identical results, predictions and hashes for a multiple-choice and a perplexity task; repeated scoring of one prepared task is identical; a different tokenizer object raises; the model's `max_length` is still checked per call.
4. Docs: the prepared form of the in-training snippet on the FineWeb-Edu page and the in-memory checkpoint page; the two functions in the Python API page.
5. Checks: tiny-model locks against `main` (requests, scores, hashes, `results.json`, predictions); `fineweb_edu` FP32 on Qwen2.5-0.5B through the prepared path equal to stage 019's number; the time of repeated scoring before and after.

---

## 4. How it was actually built

Written after the build.
