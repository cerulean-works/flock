# 004: suites scored as means of their members

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-23 (built), 2026-10-26 (review fixes) |
| Builds on | [003](003-base-easy.md) (D18: suites as data, the unweighted mean) |
| Keeps | 003's scoring, tasks as data and `results.json`; adds to the file, changes no score |
| Result | `Suite`, the `suites` section of `results.json` (schema 4), `run --suite`, the Base Easy suites holding ARC-Easy |

## 1. Goal

OLMo 3 reports Base Easy as suite means (`olmo3:base_easy:qa_rc`, `qa_bpb`, `math_bpb`, `code_bpb`). 003's D18 designed a `Suite` as data whose score is the unweighted mean of its members, as OLMES's `_macro` (`oe_eval/tasks/aggregate_tasks.py`). This stage builds it, so that later stages only add their tasks to the suites, and gives `flock-eval run` a `--suite` option that runs each task once.

**Out of scope:** weighted means, a score over all of Base Easy, and the compact CLI output for a suite run.

---

## 2. Design decisions

### 004.D1. A suite may be unscored, and suites take OLMES's names without the `olmo3:` prefix

`Suite` gains `scored: bool = True`. OLMES has no score over all of Base Easy, since an accuracy and a `bpb` don't average, so `base_easy` is an unscored suite that only groups `qa_rc` and `qa_bpb`: one name runs them all, and `results.json` lists it with `score: null`. An unscored suite holds only suites and has no `metric`; a scored suite never holds an unscored one.

Suites are named `base_easy:qa_rc` and `base_easy:qa_bpb`, OLMES's names without the `olmo3:` prefix, so each maps to OLMES's one to one. `qa_rc` and `qa_bpb` are two suites over the same task objects, with `metric=None` and `metric="bpb"`, so a run scores each task once. A member suite is scored under its own `metric`, not the outer suite's.

A scored suite's members share one scale, as `metrics.scale` defines it: every accuracy averages with another, and any other metric, such as `bpb`, only with itself. OLMES doesn't check this, its suites keep `rc` and `bpb` apart by name only, but a mean of an accuracy (higher is better) and a `bpb` (lower is better) has no meaning, so a mix raises at definition time.

### 004.D2. `results.json` is schema 4 with a `suites` section; the tracker logs `suites/<suite>`

`results.json` gains a `suites` list next to `tasks`, each entry `{suite, members, metric, score}`, every suite after the suites it holds, and empty for a run of tasks only. Each entry lists its members by name, so a reader can recompute the mean. `Results.suites` defaults to empty, so code that builds a `Results` from tasks alone keeps working.

The tracker logs each scored suite as `suites/<suite>` beside `<task>/<metric>`, and each suite's members in the run's config as `suite_members`, since a suite's score compares only between runs with the same members. No task may be named `suites`, checked in `evaluate` before the model loads.

`EvalConfig.suites` is its last field, so positional calls written before suites keep working, and `--task` is no longer required: at least one `--task` or `--suite` is.

---

## 3. Plan

1. `tasks/suite.py`: `Suite`, its checks, `tasks`, `suites` and `score`; `by_name` for the registries and for `evaluate`.
2. `tasks/base_easy.py`: the Base Easy suites over the tasks that exist; `tasks/registry.py`: `SUITES`, and `TASKS` including every task in a suite.
3. `run.py`, `results.py`, `tracking.py`, `cli.py`: run the suites' tasks once, score every suite reached, write and log them, `--suite`.
4. Tests: nested means on hand values, every definition check, the `suites` section, the CLI.
5. Docs: `tasks.md`, `results.md`, `cli.md`, `adding-a-task.md`.

---

## 4. How it was actually built

- **`Suite`** lives in `flock/eval/tasks/suite.py`, and the Base Easy suites in `flock/eval/tasks/base_easy.py`, holding only `arc_easy` until the next stage adds tasks. `SUITES` is built from `base_easy`'s own suite tree, so a suite can't be held without being registered, and `TASKS` includes every task in a suite, so adding a task to its suite registers it.
- **A member suite is scored under its own `metric`**, not the outer suite's. Nothing passes down, so a suite's score reads off its own definition. `qa_bpb` will then hold a `bpb` twin of each nested group (MMLU's 57 subjects) rather than reuse the `qa_rc` one.
- **Checks at definition time.** A suite raises on no members, a repeated member name, a member task that doesn't report the metric read from it, members on two scales, a scored suite holding an unscored one, a `metric` next to a member suite, an unscored suite with a `metric` or a direct task, and two different tasks, or two different suites, under one name. Results are keyed by name, so two definitions under one name would silently score as one. One helper, `by_name`, makes that last check for suites, for `evaluate` across `--task` and every `--suite`, and for the registries. Tasks and suites are separate namespaces, as they are in `results.json`. Each suite's `tasks` and `suites` merge its members' own tuples, so a member suite's tree is checked once, when it is built.
- **`evaluate` runs the given tasks first, then each suite's tasks not yet run**, each once, and writes every suite reached, each after its members. Both plans, and their name checks, are made before the model loads. Suite scores are means of the per-task means already on the host, so they add no GPU work; `Suite.score` recurses into member suites, so a nested suite's mean is computed again inside each parent, a few floats each time.
- **Review.** The first review asked for `Results.suites` to default to empty (an existing caller broke), for the scale check not to hard-code `bpb` as the one metric that isn't an accuracy, for a task named `suites` to be refused before the model loads, for each suite to check only its own merge, and for the tracker logging to have a test. All five are in. Tasks and suites stay separate namespaces, as in `results.json`, so a suite may share a registered task's name; a reviewer asked whether that should be forbidden, and the question is open for the maintainer. A hash over a suite's members, so two runs' suite scores can be compared in one equality, was left for the provenance stage: `results.md` states the rule (same members, same task hashes) until then.
- **Check.** The four gates pass, and the tests cover nested means on hand values, `qa_rc` and `qa_bpb` reading one scored task, the order and contents of the `suites` section, and every definition check. Scores of tasks are unchanged: the scoring path is untouched, and ARC-Easy's 19,002 request strings and hash are the same as on 003.
