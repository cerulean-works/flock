# 025: the scoring core, read top to bottom

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-04 |
| Builds on | [001](001-eval-core.md) (`ScoringModel`, `MCRequests`, metrics, results), [004](004-suites.md) (`Suite`), [022](022-prepared-tasks.md) (`Encoded`), [023](023-deterministic-scoring.md) (the SDPA pin) |
| Keeps | every request, token id, score, task hash, `results.json` and predictions file; every existing public name and signature |
| Result | `model.py`, `tasks/base.py`, `results.py` and `tasks/suite.py` lose their one-call helpers and formula helpers; `MCRequests.truncation` finds each request's doc directly; scores are byte-equal |

## 1. Goal

The core under every task was written before the code style the PR #8 and #12 reviews asked for, and no later stage could change it without conflicts across the open branches. This stage applies those rules to it in one refactor and changes no behaviour. A reader follows `loglikelihood`, `encode_requests` and `MCRequests` without jumping to a two-line helper.

**Out of scope:** any change that moves a number, adds a field, renames a public function or touches a task definition.

---

## 2. Design decisions

### 025.D1. A formula is written where it is used; a function stays only when it is a unit

A helper of one to three lines is inlined with a one-line comment, whatever its call count. A helper with one caller is inlined too, unless it is a whole block with its own reason to exist. Inlined: `_window_length` and `_input_length` (the window rule is now stated once in `loglikelihood` and once in `_logprobs`, and `_pad_batch` reads the input width off the windows it has already cut), `_move_whitespace` (at its one caller, with the lm-evaluation-harness `_encode_pair` citation), `_tokenize_contexts`, `_check_continuations`, `MultipleChoiceTask._head`, `_check_metrics` and `_check_fewshot` (into `__post_init__`), and `MCRequests._choice_index` and `_continuation_lengths` (into `choice_scores`).

Kept, because each is a unit: `_PaddedBatch` and `_pad_batch` (one batch's model inputs), `_target_logprobs` (memory chunking), `_batches` (a generator), `_score` (one forward pass), `_eval_mode` (a context manager), `_flock_hub` (a guarded import, two callers), `_prefix_token` and `_check_layout` (two callers each, each with its own error), `_shots_for`, `_hash` and `_load_shots` (each a rule with its own citation), and `_computed_metrics` (overridden by `GoldOnlyTask`).

Already done by earlier stages and not redone: `encode` returns the named tuple `Encoded` (022.D3).

### 025.D2. `MCRequests.truncation` asks which doc each request is in

It built the padded `[docs, max_choices]` request index with a `-1` sentinel, then scattered it back into a doc per request. Each doc's choices are consecutive requests, so the doc of every request is a list built in one loop, repeated for the unconditional requests. The counts are integers, so the result is exactly the same.

### 025.D3. Docstrings at the scope they describe

A module docstring that only restates what the module's one class already says goes: `tasks/suite.py`'s repeated the first sentence of `Suite`. The others in these files say something the name doesn't (`model.py` and `metrics.py`: on the scores' device; `tasks/base.py`: tasks as data; `results.py`: the predictions beside the results file) and stay. Private helpers lose their `Args:` and `Raises:` sections: `_PaddedBatch` gives each field's shape as a comment above it, and the others say in one line what they return. Public docstrings, every citation and every "why" comment stay. Prose that says `float32` says FP32, as the rest of the docs do (`model.py`, `run.py`, `compare.py`); dtype names in a tensor's shape, such as `float32 [docs]`, are code and keep their spelling.

### 025.D4. `Suite.scale` is public

A scored suite read `member._scale` of another suite, a private member of another instance. It is now the public property `scale`, the scale a suite's mean is on, which a reader of the suite may want too. An unscored suite raises `ValueError`, as its `score` does: its members may be on different scales, so the first member can't stand for all. Nothing else about suites changes.

### 025.D5. The lock

A refactor is accepted only if it changes no output. Before and after each commit, on the CPU with a tiny random Llama: scores, token ids, `tokens_cut`, rolling scores, `choice_scores`, the truncation counts and `results.json` with its predictions file are byte-equal to `main`, and so is every ARC-Easy request with its task hash. On one H200, an FP32 ARC-Easy run of OLMo 2 1B compares against the FP32 bank in `tests/eval/regression/` with 0 breaches. FP32 is the instrument that is byte-equal run to run, so any difference there would be the refactor's.

---

## 3. Plan

1. `model.py`: D1 and D3.
2. `tasks/base.py`: D1, D2 and D3.
3. `tasks/suite.py`: D3 and D4; the `float32` prose in `run.py` and `compare.py`. `metrics.py` and `results.py` already follow the rules and don't change.
4. The lock (D5) after each step, then the CI gates.

---

## 4. How it was actually built

As planned, in three code commits (the scoring model, `MCRequests` and the task checks, the suite and the prose), each locked before it was committed.

**The CPU lock.** A tiny random Llama (2 layers, a 300-token BPE trained on English and Traditional Chinese text) scores nine requests that cover a few-shot context, multi-byte text, an empty context, a whitespace-only context, a context ending in a space and one request longer than the model, at two batch budgets and three `max_length`s (24, 40 and 128, so most requests are cut at 24). Byte-equal to `main`: every log-probability, token count, `tokens_cut` and greedy flag, the rolling scores, every `encode` split, every `ChoiceScores` field and metric of a toy task, `MCRequests.truncation` and `choice_scores` on 8 random cut patterns over docs of 2 to 4 choices with and without PMI, and `results.json` with its predictions file from `evaluate`. All 19,002 ARC-Easy requests are byte-equal, and the task hash stays `ad683d6e1cd5c9f4`.

**The H200 check.** At the head of the refactor commits, ARC-Easy on OLMo 2 1B (`a1847dff`) in FP32 compares with the bank in `tests/eval/regression/` with 0 breaches. The causes printed are the commit, `sdpa_backend` and `schema_version 6 -> 7`, which the bank predates. Every metric is equal to the bank's to seven digits (acc_per_char 0.7706228, bpb 0.5391007). Since 023 pins the kernel, BF16 repeats too, so BF16 was checked against `main` directly: the predictions file is byte-equal over 2,376 docs.

**Detours.** Folding the review fixes found three things the plan had missed. `__post_init__` would have stopped at an early `return` inherited from the inlined few-shot check, so the checks are nested instead. `Suite.scale` on an unscored suite would have answered with its first member's scale, so it raises. And `results.py`'s docstring says more than the file name, so it stays. The window rule `max_length + 1` is now written in `loglikelihood` and `_logprobs`, and `_pad_batch` cuts by it. A shared helper was weighed and not kept: it was the chain this stage removes, and each place states the rule in one line beside a comment.

## 5. Known gaps

- `MCRequests.choice_scores` and `truncation` each walk the request layout `requests()` builds. The comment in `requests()` names both readers, and each has tests on a task with uneven choice counts and PMI.
