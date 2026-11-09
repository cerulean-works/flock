# 032: The window check runs before the SentencePiece preflight, which prepares each task as scoring will

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-09 |
| Builds on | [022](022-prepared-tasks.md) (`prepare_task`), [028](028-sentencepiece-tokenizer.md) (the resegmentation refusal and its preflight, 028.D5) |
| Keeps | Every task hash, every request and its token ids, every score, the schema and every error message |
| Result | A model too short for a perplexity task fails before any task is tokenized again; the preflight calls `prepare_task`; the rule that decides which tokenizers check is one property, `ScoringModel.checks_resegmentation` |

## 1. Goal

028 added a preflight to `evaluate` that encodes every multiple-choice task before scoring, so a resegmented request on a `SentencePieceTokenizer` fails before any task scores (028.D5). It shared one loop with the perplexity window check, so on that tokenizer a model too short for a perplexity task failed only after the tasks before it were tokenized, not in seconds. The preflight also copied `prepare_task`'s first three lines, and the test for "this tokenizer checks" was an `isinstance` written in `run.py` and `model.py`. This stage untangles the three without changing what any run scores.

**Out of scope:** any other tokenizer path; scoring a context with no ids, which keeps 028.D8.

---

## 2. Design decisions

### 032.D1. Every window is checked before any task is tokenized

The window check is a comparison of two integers per task, so it runs in its own loop first, as it did before 028. The preflight follows in a second loop. A run that lists a multiple-choice task before a perplexity task its model can't fit fails on the window again, before tokenizing anything.

### 032.D2. The preflight calls `prepare_task` and discards the result

The preflight now runs exactly what scoring will run, so it can't drift from it. The result is discarded, for 028.D5's reason: holding every task's tokens at once costs more than tokenizing them twice. A refusal still names its task, because the preflight adds the name to the error as before.

### 032.D3. Which tokenizers check resegmentation is one property of the model

`ScoringModel.checks_resegmentation` says whether `encode_requests` refuses a continuation that changes its context's tokens, and its docstring carries the reason: SentencePiece segments a whole string at once, and other tokenizers keep lm-evaluation-harness's slice with no check, so the refusal can't move their scores. `encode_requests` and `evaluate` both read it, so the rule and its reason live in one place and `run.py` no longer imports the tokenizer class. It is a property of the model, not of the tokenizer, because a transformers tokenizer can't carry it.

---

## 3. Plan

1. `run.py`: the window loop, then the preflight loop over multiple-choice tasks, calling `prepare_task`, only when the model checks resegmentation.
2. `model.py`: the property, read by `encode_requests`.
3. A test that a short window fails before any task is prepared on a tokenizer that checks.

---

## 4. How it was actually built

- **Checks.** The new test fails on 028's `evaluate`, which raised the multiple-choice task's refusal first. No task hash moved among the registered tasks, and the scorer and output locks show no mismatch against `main`.

### Known gaps

- **Preparing a task loads its docs twice.** As before this stage, the preflight and scoring each call `task.load`; only the tokens were ever the expensive part.
