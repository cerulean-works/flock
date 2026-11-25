# 032: a project's eval runs by suite

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-25 |
| Builds on | [025](025-eval-results.md) (D183 the model's Evals tab, D185 the compare page) |
| Keeps | the runs table, the compare page and every API route unchanged |

## 1. Goal

A project's eval runs could be read two at a time (the compare page) or as the wide runs table, one column per summary key. Reading several models across benchmarks, as a leaderboard does, meant scrolling hundreds of task columns. The table should show them by suite.

1. **One row per eval run, one column per top suite,** tinted and ranked within the column, with a mean rank to order them.
2. **One instrument:** runs and suites that weren't scored alike are never ranked together.
3. **Only what a run already holds:** its summary and config as `flock eval log` writes them, read through the project's existing snapshot.

**Out of scope:** groups or tags of runs, items across more than two runs, scores over checkpoints, and any change to what `flock eval log` writes.

---

## 2. Design decisions

### D210. The Evals preset swaps the table, and leaves the runs table as it is

**Evals** in the Table view's toolbar sets `?preset=evals`; a link with the preset and no view opens on the table. While it is on, a small table built for this (`EvalsTable`) takes the place of the runs table, fed by the same page of runs: search, filters, paging, selection and **Compare items** keep working. It isn't part of the runs table because that table is virtualized and sorted by Flock, and a mean rank isn't a stored column Flock could sort by. The preset has at most a page of runs and a few dozen columns, so it renders every cell and sorts in the browser. The button appears only once a run of the project has a `summary.suites/` column.

### D211. Columns are the top suites; a suite's scale is read from its members

Columns are the suites with a score that no other scored suite holds (`config.suite_members` under the column's hash), accuracy first, then the rest, each in name order. A suite opens to show its scored member suites beside it; a suite two opened suites hold shows once, and a suite no top suite leads to (suites that hold each other) is a top suite itself. On the bank of nine models this gives 18 columns: `base_easy:qa_rc`, `zh_tw` and `zh_cn` for accuracy, and 15 for bits per byte, among them the nine Paloma sources, since `paloma` itself has no score.

The summary doesn't say a suite's metric, but a scored suite's members share one scale (eval's `Suite`), and its score is their mean. So a suite reads as bits per byte, loss per token or perplexity when its score equals the mean of its members' values of that metric (a member task's `bpb`, or `bits_per_byte` for perplexity tasks; a member suite's score when its scale is the same), up to float rounding. Failing that, it reads as accuracy when its score lies between the means of its members' lowest and highest accuracies (`acc_*`), since a task's primary metric isn't logged. For accuracy the exact rule takes one `acc_*` metric for every member task. A scale counts for a run only when exactly one exact rule fits (a task whose bits per byte equal one of its accuracies fits two); the accuracy bounds decide only when no exact rule fits, since they are wide and a bits-per-byte score can lie inside them (`basic_skills:bpb` on the bank does). A suite that holds itself has none. Every comparable run under the column's hash must read the same scale (runs on other settings only when no comparable run scored it, for display); otherwise the scale is unknown, the column has no tint and no rank, and its scores show as plain numbers. This agrees with every suite's own `metric` in all nine bank results files, including `mt_mbpp` and `tw_text`, whose names don't say bits per byte.

### D212. Ranks are within one instrument; unlike runs are hidden

The settings most of the page's runs share, taken as a whole (dtype, attention, SDPA kernel, batch tokens), define the instrument; a tie goes to the oldest run's set. A run scored under another set is hidden behind **Show N on other settings** and, when shown, is marked and never ranked. A model's maximum length is its own (4096 to 262144 on the bank), and BOS follows the model family's rule (Gemma takes it), so neither leaves a run out: BOS is a label, as are the device and an instruction-tuned model (Instruct or Chat in the model's name). In each column, the suite hash most of the comparable runs scored it under is the column's; a cell scored under another hash shows a dash, and a cell with no hash shows its score unranked. A suite only hidden runs scored takes their hash, so its scores show when they are listed.

### D213. A rank per cell, and one mean rank as a reading order

Ranks are competition ranks (equal scores share the better rank), best first in each column's direction. A cell's tint is where its score sits between the column's worst and best. One mean rank, over every top suite with a ranked cell, orders the runs as a leaderboard does, and the table opens sorted by it, best first. It averages accuracy and bits-per-byte ranks together, so it is a convenience order for reading the table and the page says so; comparisons rest on the suites themselves. A run without a rank in one of those suites has no mean rank, so a missing suite can't raise it. Opened member suites don't count, so a parent and its members aren't counted twice.

---

## 3. Plan

1. `eval-table.ts`: the table from a page's runs, as pure data with unit tests.
2. `EvalsTable.svelte`: renders it, with opening, sorting and the hidden runs.
3. The project page: the toggle and the swap.
4. Docs: the dashboard guide's eval section.

## 4. How it was actually built

- `evalTable` takes each run's values by column key and returns the columns, the top suites and one row per run with a suite score. The component reads only the columns it needs from the snapshot (suite scores, the tasks' bits per byte, suite members and hashes, and the settings), so a bank of 641 tasks costs a few thousand reads per page.
- Unit tests cover the column order and scales (perplexity and an unknown scale included), ranks and ties, the tint, the mean rank, unlike settings taken whole and the tie on them, BOS and maximum length not counting, another or a missing suite hash, a suite only hidden runs scored, device and Instruct labels, and runs with no suite score. Component tests on the project page cover the toggle, the hidden run, the sort by mean rank and an outsider's view. A built-web test checks that a private project's preset reaches members only.

## 5. Known gaps

- Ranks and sorting are over the page's runs: a project with more eval runs than a page ranks each page alone.
- Private tasks' scores follow the project's visibility, as in the runs table: the summary has no mark of a private task, so a public project shows every score it holds.
