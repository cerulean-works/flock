# 035: One code path for the Chinese exam format

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-11 |
| Builds on | [005](005-tmmluplus.md) (TMMLU+ and the Chinese cloze format), [012](012-task-snapshot.md) (the task snapshot), [033](033-zh-cn.md) (C-Eval, CMMLU and their `Benchmark`) |
| Keeps | every task, request, gold index, task hash, suite and suite hash of TMMLU+, C-Eval, CMMLU, `zh_tw` and `zh_cn`; the snapshot is unchanged |
| Result | TMMLU+, C-Eval and CMMLU are three `Benchmark` values in one format; TMMLU+'s own `Subject` and `subject_task` are gone |

The file number is provisional. The file takes the next free number when its PR is opened.

## 1. Goal

Stage 033 gave C-Eval and CMMLU a shared `Benchmark` type but left TMMLU+ on its own copy of the same code: `ceval_doc` repeated `tmmluplus_doc` with a different question prefix, and `Benchmark.task` and `zh_cn.Subject` repeated `tmmluplus.subject_task` and `tmmluplus.Subject`. This stage moves all three benchmarks onto one type, so a fourth Chinese exam set is one declaration and a fix to the format is made once.

**Out of scope:** any change to a request, a metric, the rows 033.D8 leaves out, or how an empty continuation is handled.

## 2. Design decisions

### 035.D1. A `Benchmark` carries everything the three sets differ in

The new module `tasks/chinese_exam.py` holds `Subject`, `Category`, `Benchmark` and the row function `exam_doc`. The sets differ only in data, now fields of `Benchmark`:

| | TMMLU+ | C-Eval | CMMLU |
| --- | --- | --- | --- |
| Question prefix (through its row function) | `問題：` | `问题：` | `问题：` |
| Few-shot split | `train` (the `*_dev.csv` files) | `dev` | `dev` |
| Dev rows per subject | 5, or 3 or 4 for twelve subjects (005.D4) | 5 | 5 |
| Aggregate | mean of the category means (005) | mean of the subjects (033.D3) | mean of the subjects (033.D3) |
| Tagged subset | none | none | China specific |

`averages_categories` selects the aggregate. With it, the benchmark's suite holds the category suites; without it, the subjects directly, with each category's suite listed beside it in `<name>:all`. Each `:all` group keeps the exact members and order it had, so no suite hash moves.

### 035.D2. The Taiwan suites stay in `tmmluplus.py`

`tmmluplus:taiwan` is our selection for comparing with the Gemma-3-TAIDE cards, not a tag of the benchmark, and its member order (台灣地理, 台灣用語, 閩南語) follows the cards, not the categories. It is passed to `Benchmark.all_suite` as an extra member, which places it after the `bpb` suite as before. CMMLU's China specific tag is the benchmark's own (`src/categories.py`) and stays a `Subject` field.

### 035.D3. Row functions stay named, module-level functions

The snapshot (012) pins each task's `to_doc` by its qualified name, and a `functools.partial` has none. So `tmmluplus_doc` and `ceval_doc` remain in their modules under their old names, each one line that calls `exam_doc` with its prefix. This keeps the snapshot unchanged, names included.

### 035.D4. Tasks are built once per benchmark

`Benchmark.tasks` is a cached property: every suite of a benchmark, and the registry, hold the same task objects, as the registry test requires. `Benchmark` is a frozen dataclass without slots so the cache can live on the instance. Its categories are a tuple of `Category` values, not a `dict`, so the frozen value is hashable and can't be changed in place. `zh_cn` takes its two members from `Benchmark.suite()` instead of looking suites up by string, and the method that builds `<name>:all` is `all_suite`, so it no longer shares the name of the `Suite.suites` property.

### 035.D5. Corrections to stage 033

Stage files are not edited once merged, so two corrections to 033 are recorded here.

1. 033's "How it was actually built" says `ceval_doc` repeated `tmmluplus_doc` rather than sharing it through a parameter, "for the same reason: a `partial` has no name the snapshot can pin". That reason was incomplete: a named wrapper function can be pinned, as 035.D3 does. The duplication was a choice to leave merged TMMLU+ code alone in that stage, and this stage removes it.
2. 033's opening line, "The file number and the decision numbers are provisional", is void. 033 is the number it merged under, its decisions are `033.D1` to `033.D8`, and `main` has no stage 032.

## 3. Plan

1. Move the shared format into `tasks/chinese_exam.py` and declare the three benchmarks there as data.
2. Regenerate the snapshot and confirm it is unchanged.
3. Compare every registered task and suite with `main`.
4. Fold the two batching tests into one.

## 4. How it was actually built

- **Snapshot.** `uv run python -m tests.eval.snapshot` leaves `tests/eval/snapshot/` byte-identical: every task definition (including `to_doc` and `keep` names), every suite's members, and the rendered toy requests.
- **Registry.** A dump of every registered task (`repr`, fields in order) and every suite (members, metric, `scored`, tasks in order), in registry order, equals the same dump from `main` except for function addresses.
- **First attempt.** Building tasks on each call made suites hold equal copies of the registry's tasks, which `test_registry_suites_hold_registered_tasks` caught because it checks identity. That led to 035.D4.
- **Tests.** The batching test in `test_zh_cn.py` now scores one task of each benchmark from one row table (TMMLU+ reads C-Eval's columns), and TMMLU+'s copy is removed. The table tests read `Category` values.

## 5. Known gaps

The shared `Subject` carries `china_specific`, which only CMMLU sets. A second benchmark with its own tag would make it a general field.
