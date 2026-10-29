# 016: a compact summary from `flock-eval run`

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-29 |
| Builds on | [004](004-suites.md) (suites), [014](014-run-provenance.md) (D9: no speed for a task timed at zero) |
| Keeps | every score, request, task hash and the `results.json` layout; only what the command prints changes |
| Result | `flock-eval run` prints one line per suite and one per task; `--verbose` prints the full listing as before |

The file number and the decision number are provisional. The file takes the next free number when its PR is opened, and `016.D1` takes the next free global number after every stage merged before it.

## 1. Goal

`flock-eval run --suite base_easy` printed every metric of every task and every suite's member list: about 450 lines, most of them MMLU's 57 subjects with six metrics each. Someone running the suite wants the suite scores and each task's primary metric on one screen, and reads `results.json` for the rest.

**Out of scope:** metrics, suites, the results schema and the tracker summary. This is a presentation choice, with no metric or threshold involved.

---

## 2. Design decisions

### 016.D1. The command prints suites first, then one line per task; `--verbose` prints everything

`format_results` prints the header (model and flock commit), then one line per suite with its score (or `unscored` for a suite that only groups others), the metric it averages (`bpb`, or `primary` for each member's primary metric, so a bpb mean is never read as an accuracy) and its member count, then one line per task with its primary metric's value and name and the number of docs scored. Suites and tasks share one name column, so every score sits in one column. `results.json` holds each suite after its members; the summary prints each suite before its members, in declared order, starting from the suites that no other suite holds, in command-line order, so `base_easy` is the first line and its parts follow it. A suite held by two others prints once, under the first, and so does a suite given with `--suite` that another given suite also holds.

`--verbose` restores the earlier print unchanged: per task its doc count, speed and the start of its hash, then every metric with the primary first, then each suite with its member names. A flag rather than a second command, because it is the same run read at another depth. `results.json`, the predictions and the tracker run are the same with or without it.

*Prior work:* the default follows OLMES (`5a51f50`), which ends a run by logging "Summary of primary scores": one `task: score` line per task, with the aggregate tasks put in front (`oe_eval/run_eval.py:1039-1046`). The verbose print is closer to lm-evaluation-harness v0.4.3, whose table has a row for every metric of every task, then a second table for groups (`make_table`, `lm_eval/utils.py:311`, printed at `lm_eval/__main__.py:444-446`). The member and doc counts, the metric column, the shared name column and the suite order are our own choice, with no precedent.

---

## 3. Plan

1. `cli.py`: the `--verbose` flag, and `format_results(results, verbose=False)` with the summary as default.
2. Tests: the summary and the verbose print on hand-built `Results` with known values, a run of tasks only, and the CLI with and without `--verbose`.
3. Docs: `cli.md` (what the command prints, the flag), `quickstart.md` and `results.md`.

---

## 4. How it was actually built

- **Lock.** The CLI ran on the tiny model at `main` and on this branch, once with the toy task and once with a toy suite of two scored suites under an unscored one. The predictions files are byte-equal, and `results.json` differs only in the `--out` path inside `command` (each run wrote to its own directory) once `created_at`, `seconds` and `docs_per_second`, which a clock sets, are masked.
- **Review.** One round found seven things. Fixed: suite lines now name their metric (a `bpb` suite looked like an accuracy); the order was first the results order reversed, which put the last of several `--suite`s first and reversed each suite's members, and is now a walk from the suites asked for; an empty `Results` no longer fails on `max`; two test names and assertions. Declined: splitting the two layouts into two functions with a shared score helper (the helper would be a one-line formula, which this codebase inlines), and sizing the score column from the scores (every metric today is a mean below 10, so eight characters hold it).
- **Second review round** found nothing serious. The docs now say which suites start the order (one held by another prints under it). Declined: printing a `bpb` suite's members by bpb (each task shows its primary metric by design, and `--verbose` and `results.json` hold the rest), grouping task lines under their suites, padding the metric column, and a name set for the walk (a dozen suites at most).
- **Shared test results.** The hand-built provenance and `task_result` that the tracker tests used moved to `tests/eval/toy.py`, so the summary tests build their `Results` from the same pieces.
