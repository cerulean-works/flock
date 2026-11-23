# 029: a row of suite squares over a pair's items

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-24 |
| Builds on | [028](028-eval-item-links.md) (D199 rows with margins, D202 the counts table) |
| Amends | D198 (the margin and its order are computed by one function for the list and the squares); 027's summary line (two runs' scores and flips are the counts table's) |

## 1. Goal

The item list shows twenty docs of one task at a time. A reader comparing two runs can't see the shape of the comparison: where the runs agree and how surely, whether the docs one run lost were near misses or confident mistakes, and which suites hold the difference.

1. **A picture of each suite.** For every suite of accuracy tasks both runs scored, one small scatter plot of its docs, all in one row above the list.
2. **Tied to the list.** A plot's quadrants are the list's four groups: the pointer on one lifts the list's button for it, the pointer on a button lifts the quadrant, and a click shows that suite and group in the list.

**Out of scope:** more than two runs, clustering, a checkpoint axis (all need groups of runs, which come later), any change to how eval reads or scores.

---

## 2. Design decisions

### D203. `GET .../items/suites` answers the whole row for a pair, computed once and kept

- `GET .../runs/{run}/items/suites?with=` answers `{"suites": [{"suite", "tasks", "counts", "dots"}]}`: one entry per suite both runs recorded under one hash, that is scored by its tasks' own metrics (not `mmlu:bpb`, which reads bits per byte from the same tasks) and holds a task of its own (not `tmmluplus` or `zh_tw`, which only group others: their groups get squares). `tasks` is every task under it, nested suites' too, both runs scored under one hash with an accuracy that ranks choices as the primary metric. `counts` is the number of its docs in each cell of the diff; `dots` holds up to 800 docs as `[A's margin, B's margin, cell, task, place, id]`, taken evenly through each cell in the list's default order (each cell its share of the 800, never more in all), margins rounded to 3 decimals; `task` indexes `tasks` and `place` is the doc's in that task's cell in the list's default order, so a dot opens its doc.
- **Two runs only**: `with` is required. One run's docs have no cells.
- **One formula.** The margins and their order come from `_with_margins`, which the list's route now calls too.
- **Each task once.** Suites share tasks (`base_easy:qa_rc` holds all of MMLU), so every task is computed once and its docs go to each suite that holds it. Each file is read once per request, so the four cells come from the same rows.
- **Cost, measured** on two whole-bank runs (OLMo 2 1B and Llama 3.2 1B, schema 12): 10 squares over 277 tasks and 143,049 docs; 25.4 s with eval's reader on local folders, about 46 s through the app's store. So the answer runs on a worker of its own (`SQUARES`, one thread), never holding up the list's and docs' reads in `DIFFS`, and is kept for the last 8 pairs, keyed by both runs' results files and the viewer. A finished run's files don't change, and a results file that does is a new key. A pair's first visit waits; every later one is instant.
- Access is the list's: a task whose predictions a run lacks or the viewer can't read (a private dataset's, outside the organization) is left out of its suites, which is why the viewer is in the key.

### D204. Equal small scatter plots in one row

- **A scatter, not blocks or bands.** The first build drew each cell of one task as a block of marks, A's margin on a mark's top half and B's on its bottom; the pattern was hard to catch (two halves of a 4 px mark, thousands of times) and the blocks restated the counts. The second drew one wide scatter per task, then one per suite at its column's width, which made a page of plots of every size. A small square per suite, all the same size (112 px), keeps one picture per suite and lines them up: the zero lines split each into the four cells, and the dashed diagonal is where the runs are equally sure, so a dot far from it is a doc they disagree on, near an axis a near miss.
- **No numbers on the axes.** Each plot reaches its suite's 98th percentile of absolute margins, the same either way so the diagonal is at 45 degrees; a margin past it sits on the edge. Margins under a length-normalized metric are small, and one fixed scale would crowd such a suite into the middle. The scale differs between squares and isn't shown: the shape is what a square is for, and the list has the numbers.
- **The docs one run alone got right are drawn in the reading blue, on top;** the rest are grey and see-through, so a crowd reads darker.
- **One row,** scrolling sideways when the squares don't fit, resting on a square's edge (`snap-proximity`), and spanning the page on the compare page as the list's table does. Padded so the marked square's ring isn't cut by the scrolling box. While the row is computed, its place is held by grey squares, so the list doesn't jump when it arrives.
- Canvas, not one element per doc. Colours are read from the theme tokens and redrawn when the page turns light or dark, as `LinePlot` does.

### D205. A quadrant is the list's group, a dot its doc

- One square is marked with a ring: the one last clicked while it holds the list's task, else the first that does. Suites share tasks (`base_easy:qa_rc` holds all of MMLU), so marking every square that holds the task ringed two at once.
- The pointer on a quadrant of any square washes it in the reading blue, fades the other cells' dots, names the group and its count, and lifts the list's button for that group. The pointer on a button lifts its quadrant in the marked square.
- A click on a quadrant shows that group in the list, for the task shown if the suite holds it, else the suite's first task, from the first page.
- **A dot within 4 px of the pointer is its doc:** ringed, with a tooltip of its task, id and both margins whose corner follows the pointer (fixed to the window, so the scrolling row can't clip it, and turned to the pointer's left near the window's edge). A click on it opens the doc in the list: its task and cell in the default order, at the page `place / 20 + 1`, the focus moving to its row once the page arrives. 028's `landing` (where j and k land on a turned page) holds a function picking the row from the page, so j, k and a dot share one path.
- Not reachable by keyboard: the task picker and the group buttons do the same.
- With two runs, the list's line of scores and flips is gone: the counts table under the list says the same per task (D202).

---

## 3. Plan

1. The route and its tests (`app/api/src/api/runs.py`, `tests/app/test_evals.py`).
2. The relay, the type, `ItemsMap` (the row) and `ItemsSquare` (one square), their place in `EvalItems`, a spec.
3. Docs: the dashboard guide and the API reference.

## 4. How it was actually built

- `run_items`' margin loop and sort moved, unchanged, into `_with_margins(found, sort)`; `_accuracy_suites` picks the suites and their tasks from the two results files.
- Built in four looks with the dashboard's owner on a preview of the bank pair: blocks per task, one wide scatter per task, a grid of scatters per suite at the column's width (plots of every size), then this row of equal squares tied to the group buttons.
- The squares were first fetched one suite per request, each recomputing the tasks it shares with others on the list's own workers; on the bank pair that held the page for minutes. One request per pair, its own worker and the kept answer replaced it.
- Review of the first build asked for one read of each file per request, kept here, and for the plot not to hold the page's grid at an old width; the row is now a scrolling box, which a grid lets shrink.
- Review of the row asked for the cache key to hold each run's current access rather than the viewer (a viewer who left an organization would still have been answered from private predictions), for each suite to be opened once (an uploaded file can nest suites in a loop, which held the one worker forever), for an answer missing a task a run is still uploading not to be kept, for each task's files to be let go after it, for a suite's `tasks` to hold only those it draws, and for the 800 dots to be a budget across the cells. All made.

## 5. Known gaps

- A pair's first visit computes every suite (about 46 s on two whole-bank runs). Reading each task's predictions once for all four cells needs a change to eval's `items()`, outside this stage.
- The kept answers live in the API's memory: a restart forgets them, and each viewer has their own.
- The squares have no text: a reader clicks through to the list to read docs.
