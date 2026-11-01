# 017: one Traditional Chinese number of each kind

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-29 |
| Builds on | [003](003-base-easy.md) (D18: a suite's score is the unweighted mean of its members), [004](004-suites.md) (D1: suites of suites on one scale), [005](005-tmmluplus.md) (D2: TMMLU+'s suites), [009](009-tw-mock-exams.md) (D5: the exam set's suites), [010](010-drcd.md) (`drcd_bpb`) |
| Keeps | every task, request, task hash and score; adds two suites |
| Result | `zh_tw` (accuracy) and `zh_tw:bpb` in `SUITES`, the suites table in `tasks.md` |

## 1. Goal

Three Traditional Chinese benchmarks are registered: TMMLU+ (`tmmluplus`, `tmmluplus:bpb`), DRCD (`drcd_bpb`) and the mock exam set (`tw_mock_exams`, `tw_mock_exams:bpb`). Nothing scores them together, so a run comparison has Base Easy's suite scores for English and no single number for Chinese. This stage adds one suite per scale, so that every model and checkpoint gets one Chinese accuracy and one Chinese `bpb` from the same members.

**Out of scope:** new tasks, DRCD with five examples, an unscored group that runs both suites under one name, and any change to a member.

---

## 2. Design decisions

### 017.D1. Two suites, `zh_tw` and `zh_tw:bpb`, with no precedent to follow

No reference suite exists for Traditional Chinese. OLMo 3's Base Easy (2512.13961) is English only. TMLU (2403.20180) and TMMLU+ each report their own subjects and a plain average over them (TMMLU+'s card over its four categories), and neither combines with another benchmark. So the family follows this package's own suite rules: members averaged without weights, as OLMES's `macro` aggregate (003.D18), on one scale per suite (004.D1).

| Suite | Members | Metric |
| --- | --- | --- |
| `zh_tw` | `tmmluplus`, `tw_mock_exams` | each member suite's score (`acc_per_char` means) |
| `zh_tw:bpb` | `tmmluplus:bpb`, `drcd_bpb`, `tw_mock_exams:bpb` | `bpb` |

`zh_tw:bpb` is the number to decide on for small checkpoints, whose accuracy is still near chance (009.D5, after OLMo 3 sec. 3.3.2). `zh_tw` is for models above chance. `drcd_bpb` reports only `bpb`, so it is a member of `zh_tw:bpb` alone. The exam set's `tw_mock_exams_english` (英文) is an English exam in MMLU's format, so one tenth of `tw_mock_exams` measures English as Taiwan's senior-high exams test it, not Chinese: 1/20 of `zh_tw` and 1/30 of `zh_tw:bpb`. It stays in, since the exam set is one benchmark and its suite is the member; a reader comparing models on Chinese alone reads the subject scores. Both suites read the private exam set, so they run only with access to `cerulean-works`.

### 017.D2. Each benchmark is one member, through its scored suite, and members weigh equally

The members are the benchmarks' scored suites, not their subject tasks. TMMLU+ has 66 subjects and the exam set 10, so a mean over subjects would make `zh_tw` mostly TMMLU+. Through their suites each counts once, as `mmlu` is one member of `base_easy:qa_rc`. `tw_mock_exams:all` can't be the member: it is unscored (009.D5), and a scored suite never holds an unscored one (004.D1).

`tmmluplus` is itself the mean of its four category means (005.D2), so a TMMLU+ subject's weight inside `zh_tw` depends on its category's size. `tmmluplus:taiwan` is not a member: its three subjects are already in `tmmluplus`, and a second copy would count them twice.

---

## 3. Plan

1. `tasks/zh_tw.py`: the two suites over the existing suites and `drcd_bpb`; `tasks/registry.py` registers their trees.
2. A test of the members and of the equal weights on hand values.
3. The snapshot, regenerated: it gains the two suites and nothing else.
4. Docs: two rows and a paragraph in `tasks.md`.

---

## 4. How it was actually built

- **As planned.** The suites are two lines in `tasks/zh_tw.py`, and the registry adds `ZH_TW.suites` and `ZH_TW_BPB.suites` beside the other trees. `TASKS` is unchanged, since every member task was already registered.
- **Check.** The regenerated snapshot differs from `main` by the two new suite entries only: every task entry and task hash, and the toy requests, are byte-equal. A task run and a suite run on the tiny CPU model write the same `results.json` with and without the new suites registered, once the clock fields and the output path are masked, and byte-equal predictions. The test gives every TMMLU+ subject 1.0 and every exam subject 0.0 and finds `zh_tw` at 0.5, which only holds if each benchmark weighs one member.
- **Review.** One round asked for the citations above (003.D18 for the mean, TMLU next to TMMLU+), for the English subject to be stated, for the docs not to read as a flat mean over 76 subjects, and for the unchanged `results.json` to be measured rather than argued. All are in. A reviewer also suggested building `TASKS` from every registered suite's tasks, so a suite can't hold an unregistered task by construction; that changes the task list's code, which this stage keeps unchanged, so it is left for a later cleanup.
