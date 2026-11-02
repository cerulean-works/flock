# 022: prepare a task once, score it at every evaluation step

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-02 |
| Builds on | [001](001-eval-core.md) (`score_task`, `ScoringModel`), [008](008-paloma.md) (`PerplexityTask`, rolling windows), [019](019-fineweb-edu-dev.md) (`fineweb_edu` and the in-training snippet) |
| Keeps | every request, token id, score, task hash, `results.json` and predictions file; `score_task(model, task)` and `evaluate` as they are |
| Result | `prepare_task` loads and tokenizes a task once; `score_prepared` scores it with a model that has the same tokenizer, with results byte-equal to `score_task`, and saves the 1 to 2 s of host work each call spent before |

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

- **Code.** `model.py` first (`Encoded`, `encode_requests`, `tokenize`, and the optional ids on `loglikelihood` and `rolling_loglikelihood`), then `run.py` (the two prepared dataclasses, `prepare_task`, `score_prepared`, and `score_task` as the two composed). `evaluate`, the CLI and `score_task`'s signature are unchanged.
- **Tests.** `tests/eval/test_prepared.py`: a multiple-choice and a perplexity task give the same `TaskResult` (all fields but `seconds`) and the same predictions bytes through `score_task` and through two `score_prepared` calls on one prepared task; scoring a prepared task fails if it calls `Dataset.load`, the task's `load`, `requests` or `task_hash`, or the model's `tokenize` or `encode_requests` (the task methods are patched too, because a perplexity task's split is cached after its first load and a reload would never reach the dataset); a copy of the tokenizer is refused, a new `ScoringModel` around the same tokenizer object is accepted; a model with a shorter `max_length` is refused at the call.
- **Locks.** On a tiny Llama model, `score_task` on `main` and `prepare_task` plus three `score_prepared` calls on this branch give equal results (every field but `seconds`, metrics compared by `repr`) and byte-equal predictions, for a multiple-choice task with shots and truncated contexts and for a text task with window 8. `evaluate`'s `results.json` and predictions for the toy task are byte-equal to `main`.
- **Checks on real weights** (one H200, `batch_tokens` 32768). Through the prepared path in FP32, `fineweb_edu` on Qwen2.5-0.5B (`060db649`) gives perplexity 10.958357925241533 and the task hash of stage 019's run, every metric equal to the last bit, and `arc_easy` on OLMo 2 1B (`a1847dff`) gives every metric of `main`'s FP32 run to the last bit.
- **Time.** In BF16, five `score_task` calls on `fineweb_edu` with Qwen2.5-0.5B took 42.1 s and then 32.5 to 33.1 s each; one `prepare_task` took 1.5 s, and five `score_prepared` calls 30.9 s each. On `arc_easy` with OLMo 2 1B, ten `score_task` calls took 18.9 s and then 13.1 to 13.4 s; `prepare_task` took 1.0 s and each `score_prepared` 12.1 s. So a step saves about 1.8 s on `fineweb_edu` and 1.1 s on `arc_easy`, the host work `seconds` never counted. The forward passes dominate at these sizes: the saving is about 5 to 8 percent of a step. All calls of one task gave the same metrics.
- **Not changed.** The token ids are Python lists of ints, as the scorer takes them, which is 220 MB of host memory for `fineweb_edu` with the Qwen2.5 tokenizer (5,414,398 tokens) while a run holds the prepared task. `score_task` now prepares before it checks that the predictions file is new, so that error comes after the load. `_score_perplexity` still counts each doc's UTF-8 bytes at every call (12 ms for its 27 MB), and a tokenizer changed in place after preparing is not detected.
