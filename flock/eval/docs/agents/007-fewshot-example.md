# 007: a doc's text as a few-shot example

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-24 |
| Builds on | [003](003-base-easy.md) (choices as pairs, D19's gold-only math and code) |
| Keeps | D16 (a doc lists its choices as pairs), D17 (few-shot examples), D22 (how parity is measured) |
| Result | `MCDoc.example`; HumanEval's requests identical to OLMES's; every other task's requests and hash unchanged; a correction to D22's float32 padding figure |

The file number and the decision number are provisional. The file takes the next free number when its PR is opened, and `007.D1` takes the next free global number after every stage merged before it.

## 1. Goal

A few-shot example is rendered as its gold choice's context and continuation (D16, D17). HumanEval breaks that. OLMES scores a HumanEval doc on its canonical solution right after the prompt, but shows the same doc as an example with a space between the two (`doc_to_target` in `oe_eval/tasks/oe_eval_tasks/codex_humaneval.py`, while the scored text goes through `construct_basic_generation_requests`, which adds no space to text that is already indented). One `to_doc` can't render both, so a doc needs a way to read differently as an example.

**Out of scope:** a per-task example hook, and any change to how a doc is scored.

---

## 2. Design decisions

### 007.D1. `MCDoc.example: str | None = None`

`MCDoc` gains an optional `example`: the doc's full text when another doc shows it as a few-shot example. When it is `None` the example is the gold choice's context and continuation, as before, so every existing task renders exactly as it did. HumanEval's `to_doc` sets it to the prompt, a space, then the solution.

The field sits on the doc rather than the task because the text is the doc's: a task-level hook would take the doc and rebuild the same string. It only changes how a doc reads as a shot, never how the doc itself is scored. The task hash covers it through the requests it produces, so tasks that don't set it keep their hash.

### 007.D2. `GoldOnlyTask` is a task scored on its gold text alone

The 26 math and code tasks share one shape: a doc with one choice, scored by `bpb` only, with no unconditional context, since an accuracy over one choice is always 1 and pointwise mutual information (PMI) has nothing to compare. `GoldOnlyTask`, a subclass of `MultipleChoiceTask`, fixes that shape: `primary_metric`, `metrics` and `unconditional_context` are set by the class and can't be passed, so the three task modules declare only what differs, and the reason is written once, on the class. Scoring is unchanged, since only those three defaults differ, so every task's requests and hash are the same as with the full constructor.

The first version was a classmethod, `MultipleChoiceTask.gold_only`. Review asked for its own task type, since a gold-only task reads differently enough from a multiple-choice one that the name should say so.

The suites follow OLMES's `olmo3:base_easy:math_bpb` and `code_bpb` (`oe_eval/configs/task_suites.py`), both reading `bpb`. `mt_mbpp`, the 17 languages, is one member of `code_bpb` beside HumanEval and MBPP, as OLMES's `mt_mbpp_v2fix` suite is, so each language has a 51st of the weight.

---

## 3. Corrections to 003

**D22, float32 padding.** D22 says float32 padding is invisible, citing 001's measurement of at most 1.8e-4 nats. Over the 14,164 math and code requests on `allenai/OLMo-2-0425-1B` (final checkpoint, float32), one request moved further: `mt_mbpp_typescript` doc 113 scores -114.765076 nats in its batch of 32768 tokens and -114.763397 alone, a gap of 1.7e-3. OLMES, which scores one request per forward pass, gives -114.763611: 1.46e-3 from our batched score and 2.1e-4 from our unbatched one. So float32 padding can move one request by about 2e-3 nats, and D22's per-request 1e-3 check is read with that in mind: a request over it that falls back inside it when scored alone is our batching, not the task.

---

## 4. Plan

1. `tasks/base.py`: the `example` field and its docstring; `_head` renders `example` when it is set; `GoldOnlyTask`.
2. `tasks/minerva_math.py`, `tasks/humaneval.py`, `tasks/mbpp.py`: the 26 tasks, built as `GoldOnlyTask`; `humaneval_doc` sets `example`; Minerva's written-out examples in `tasks/shots/minerva_math.py`; the two suites in `tasks/base_easy.py`.
3. Tests: a shot with `example` renders it in place of its gold choice, and the scored doc is unchanged.
4. Docs: `adding-a-task.md` and `tasks.md` describe the field and HumanEval's quirk.

---

## 5. How it was actually built

- **Lock.** Every request string of the 26 math and code tasks is identical to OLMES's, and ARC-Easy's 19,002 requests and hash are unchanged by the field.
- **Only HumanEval sets it.** Minerva MATH's four examples are literal rows, written out with OLMES's text, so they need no `example`. MBPP and the 17 multilingual MBPP languages render their examples from the gold choice.
- **The 1e-3 request.** The TypeScript request was found in the float32 parity run and rescored alone on the same checkpoint to separate batching from the task. Every task's `bpb` matches OLMES's to 1.4e-7 in float32.
