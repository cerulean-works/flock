# 029: a map of every doc of a pair

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-23 |
| Builds on | [028](028-eval-item-links.md) (D199 rows, D201 one open doc, the `landing` of j and k) |
| Amends | D198 (the margin and its order are computed by one function for the list and the map) |

## 1. Goal

The item list shows twenty docs at a time. A reader comparing two runs can't see the shape of a whole task: how many docs sit in each cell, whether the docs one run lost were near misses or confident mistakes, and where the other run stands on them.

1. **One map per task.** Every doc of the task the list shows, for the two runs, drawn at once above the list.
2. **The list's terms.** The list's cells and margins, the same docs in the same order.
3. **Click to read.** A clicked mark opens that doc in the list.

**Out of scope:** more than two runs, clustering, a checkpoint axis (all need groups of runs, which come later), any change to how eval reads or scores.

---

## 2. Design decisions

### D203. `GET .../items/map` answers every doc of a task for two runs, by cell, in the list's order

- `GET .../runs/{run}/items/map?task=&with=` answers `{"cells": [{"keep", "ids", "margins"}]}`, one entry per cell of eval's diff in eval's order (`CELLS`), each holding its doc ids in the order `.../items` lists that cell by default (the largest gap between the two margins first, D198) and each doc's A and B margins rounded to 4 decimals. Columns of ids and pairs, not objects per doc, so HellaSwag's 10,042 docs are 234 KB.
- **Two runs only**: `with` is required. One run's docs have no cells to draw, and a map of one run's margins tells little a sorted list doesn't.
- **One formula.** The margins and their order come from `_with_margins`, which the list's route now calls too, so the map and the list can't disagree on where a doc sits.
- Access and refusals are the list's: it reads through `eval_readers`, and a private task's map reaches the org's members only.
- **Each file is read once per request** for the four cells, so the cells are cut from the same rows: a predictions file replaced between two reads would otherwise put a doc in two cells or none.
- **Cost**, measured with eval's reader on two whole-bank runs (OLMo 2 1B and Llama 3.2 1B, schema 12), reading the files once per cell: HellaSwag 2.45 s, ARC-Challenge 0.26 s (1,172 docs, 42 KB), MMLU college biology 0.04 s. One read instead of four leaves `items()`'s own work per cell.

### D204. One scatter plot: a dot per doc at A's margin across and B's up

- **A scatter, not blocks.** The first build drew each cell as a block of small marks, A's margin on a mark's top half and B's on its bottom. At the first look the pattern was hard to catch: the eye had to compare two halves of a 4 px mark, thousands of times, and the blocks restated the counts above them. A scatter puts the relation itself on the page: the zero lines split the plot into the diff's four cells, each labelled with its count in its corner, and the dashed diagonal is where the runs are equally sure. A dot far from it is a doc they disagree on; near an axis, a near miss.
- **The docs one run alone got right are drawn in the reading blue, on top;** the rest are grey and see-through, so a crowd reads darker. Their place, not their colour, says which cell they're in.
- **Both axes reach the task's 98th percentile of absolute margins**, the same either way so the diagonal is at 45 degrees; a margin past it sits on the edge. Margins under a length-normalized metric are small (ARC-Easy's 98th percentile is 0.85, HellaSwag's smaller still), and one fixed scale would crowd such a task into the middle.
- **A fixed height (300 px) and the column's width**, so the plot never moves the list as it loads and never widens the page (`contain: inline-size`).
- A doc with a margin missing (`acc_greedy`, one choice) has no place and isn't drawn.
- Canvas, not one element per doc: ten thousand elements would cost layout on every hover. Colours are read from the theme tokens and redrawn when the page turns light or dark, as `LinePlot` does. Hovering names the nearest dot's doc and both margins; a click takes the nearest within 6 px.

### D205. A click opens the doc at its place in the list

- A dot knows its cell and its index in the cell's default order, so its page is `index / 20 + 1`. A click sets the list's cell, the margin order and that page, opens the doc, and once the page arrives moves the focus to its row, which scrolls it into view.
- 028's `landing` (where j and k land on a turned page) now holds a function picking the row from the page, so j, k and the map share one path. A doc already on the page shown opens at once.
- The plot isn't reachable by keyboard: the list under it holds the same docs and its keys.

---

## 3. Plan

1. The route and its tests (`app/api/src/api/runs.py`, `tests/app/test_evals.py`).
2. The relay, the type, `ItemsMap` and its place in `EvalItems`, a spec.
3. Docs: the dashboard guide and the API reference.

## 4. How it was actually built

- `run_items`' margin loop and sort moved, unchanged, into `_with_margins(found, sort)`; `run_items_map` calls it with the default order for each cell.
- `ItemsMap` fetches the map when the task changes and draws each cell from an attachment that re-runs on the width, the task's scale or the theme. A failed or refused map draws nothing: the list under it says why.
- The test checks each cell's ids and margins against the list's own default pages, so the two orders are one.
- Review asked for the single read (`functools.cache` over each run's reader for the request) and for the map not to hold the grid at its old width after a narrowing (`contain: inline-size`). (The review's third point, splitting a tall cell's bitmap, went with the blocks: the scatter has one bitmap of fixed height.)

## 5. Known gaps

- `items()` still matches the two runs' rows once per cell; sharing that work across cells needs a change to eval, outside this stage.
- The map has no text: a reader opens a doc to see it.
