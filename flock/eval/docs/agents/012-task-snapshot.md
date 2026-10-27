# 012: task definitions and toy requests pinned in CI

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-27 |
| Builds on | [001](001-eval-core.md) (D7: the task hash), [004](004-suites.md) (suites) |
| Keeps | every task, suite, request and score; adds tests only |
| Result | `tests/eval/snapshot/`: `tasks.json`, `toy_requests.json` and the command that rewrites them |

## 1. Goal

Issue #23 asks for regression tests, so that a change which moves a task's numbers is caught before it merges. CI runs on CPU with no network, and a task's hash covers every request it renders, so pinning the real hashes would download every dataset on every pull request. What CI can check offline in seconds is what a task *is*: the fields of its definition, and the exact strings the prompt code renders. Every regression found by hand so far was a change in a request string, none a change in a score.

**Out of scope:** scores, GPU jobs and banked results (a later stage compares two `results.json` files); downloading datasets in CI; changing any task.

---

## 2. Design decisions

### 012.D1. Every registered task and suite is pinned by its definition, in one readable JSON file

`tests/eval/snapshot/tasks.json` holds, for every name in `TASKS`, the task's type name, its `dataset` and `fewshot` fields, `primary_metric`, `metrics`, `unconditional_context`, `description` and the qualified name of `to_doc`; and for every name in `SUITES`, its members by name, its `metric` and `scored`. Few-shot rows written out in a task's module are pinned by their count and the sha256 of their JSON, since the rows themselves are long and already reviewed where they are written. The file has sorted keys and a two-space indent, so a change reads as a small diff in review.

The fields are read off the dataclasses rather than listed by hand, so a field added to `Dataset`, `Fewshot` or a task reaches the snapshot with no edit here. A field that JSON can't hold, such as a function, fails the build loudly until this code says how to pin it.

A test builds the snapshot from the live registry and compares it with the file. A difference fails naming the task or suite and the field (`task arc_easy: dataset.revision was '210d...', is now 'ffff...'`), followed by the command that regenerates the file.

### 012.D2. The toy tasks pin the rendered requests of every prompt path

`tests/eval/snapshot/toy_requests.json` holds every request the toy tasks of `tests/eval/toy.py` render (doc id, context, continuation, and whether the choice is gold) and each toy task's hash. There is one toy task per prompt path in the core: few-shot examples by id from another split with the unconditional context, ids from the scored split with `num_shots` and a `description`, examples written out as rows, and zero-shot without the unconditional context. The toy rows hold Chinese text, so multi-byte strings are pinned too.

Pinning the hash as well as the strings means a change to how the hash is computed shows up here, before it splits banked results from new ones. A prompt path added later (a task scored on its gold text alone, Chinese question prefixes) adds its toy task, and its pinned requests, in the pull request that brings it.

### 012.D3. One command regenerates both files, and a regenerated snapshot is a reviewed change

`uv run python -m tests.eval.snapshot` rewrites both files from the code. It lives with the tests, so `flock-eval` gains no command. A pull request that changes a definition or a rendered string on purpose regenerates the snapshot in the same pull request, and the reviewer reads the JSON diff as part of the code: it is the record of what changed for every task.

### 012.D4. What the snapshot does not catch

A change inside a task's `to_doc` that alters what it renders from real rows is invisible here, since CI never loads the real rows; nor is a change of upstream data under the same revision, which the pinned revision rules out. Each task's own rendering test (a hand-written row mapped to the expected doc) covers `to_doc`, and the hash recorded in every `results.json` catches the rest when two runs are compared. No harness we follow snapshots its task registry: OLMES and lm-evaluation-harness pin dataset revisions and test request rendering only, so this layer is our own.

---

## 3. Plan

1. `tests/eval/toy.py`: the toy rows' loader shared with `conftest.py`, and one toy task per prompt path.
2. `tests/eval/snapshot/__init__.py`: build both snapshots and list the differences between two of them; `__main__.py` writes the files.
3. `tests/eval/test_snapshot.py`: both comparisons, and the failure path on a registry with one changed revision.
4. Docs: a paragraph in `adding-a-task.md`.

---

## 4. How it was actually built

- **`tests/eval/snapshot/`** is a package: `__init__.py` builds both snapshots (`definitions`, `toy_requests`) and lists the differences between two of them (`differences`), and `__main__.py` writes the files, so `python -m tests.eval.snapshot` needs no entry point in `flock-eval`. Both builds return what the file would read back (JSON turns tuples into lists), so the comparison is plain equality per field.
- **Differences are listed per leaf**, with a dotted path into nested fields (`dataset.revision`, `fewshot.ids.2`, `requests.5.context`), every one at once, then the regenerate line. A removed or added entry is one line. A test replaces ARC-Easy's revision in a copy of the registry and checks the exact message, and another edits one written-out row and checks that only the digest moves.
- **`to_doc` must be a plain function.** It is pinned as `module.qualname` (`flock.eval.tasks.arc.arc_doc`), and anything else, such as a `functools.partial`, raises when the snapshot is built, since it has no stable name.
- **The toy rows' loader** moved into `toy.py` as `load`, used by `conftest.py`'s fixture and by `toy_requests`, which patches `Dataset.load` with it so the real `load` and few-shot selection run. The four toy tasks render 49 requests and take milliseconds; the test run is dominated by importing `torch`. Few-shot rows written out (`Fewshot.rows`) were already on `main`, so the toy tasks cover them here rather than in a later stage.
- **Check.** The four gates pass, the docs build and their links resolve, and regenerating on a clean tree rewrites both files byte for byte.
