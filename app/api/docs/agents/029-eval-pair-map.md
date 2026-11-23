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
2. **The list's terms.** Grouped by the list's cells, in the list's order, tinted as the list tints margins.
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

### D204. One canvas per cell, a mark per doc, two halves per mark

- Each doc is a small mark, A's half on top and B's below, wrapped across the width; each cell has its own canvas under its label and count, so the four groups read apart. Marks are 9 px apart, or 5 px for a task of more than 2,000 docs.
- **The tint is the list's**: green above zero, red below, over the muted ground, so a reader who knows the list reads the map. Its strength is the margin over the task's 90th percentile of absolute margins: margins under a length-normalized metric are small (HellaSwag's 90th percentile is 0.25), and one fixed scale would wash such a task out. The legend shows the scale.
- Colour is not the only cue for the outcome: the cell a mark sits in says it.
- Canvas, not one element per doc: ten thousand elements would cost layout on every hover. Colours are read from the theme tokens and redrawn when the page turns light or dark, as `LinePlot` does.
- Hovering a mark names its doc and both margins.

### D205. A click opens the doc at its place in the list

- A mark knows its cell and its index in the cell's default order, so its page is `index / 20 + 1`. A click sets the list's cell, the margin order and that page, opens the doc, and once the page arrives moves the focus to its row, which scrolls it into view.
- 028's `landing` (where j and k land on a turned page) now holds a function picking the row from the page, so j, k and the map share one path. A doc already on the page shown opens at once.
- The map isn't reachable by keyboard: the list under it holds the same docs and its keys.

---

## 3. Plan

1. The route and its tests (`app/api/src/api/runs.py`, `tests/app/test_evals.py`).
2. The relay, the type, `ItemsMap` and its place in `EvalItems`, a spec.
3. Docs: the dashboard guide and the API reference.

## 4. How it was actually built

- `run_items`' margin loop and sort moved, unchanged, into `_with_margins(found, sort)`; `run_items_map` calls it with the default order for each cell.
- `ItemsMap` fetches the map when the task changes and draws each cell from an attachment that re-runs on the width, the task's scale or the theme. A failed or refused map draws nothing: the list under it says why.
- The test checks each cell's ids and margins against the list's own default pages, so the two orders are one.
- Review asked for the single read (`functools.cache` over each run's reader for the request) and for the map not to hold the grid at its old width after a narrowing (`contain: inline-size`). A cell's bitmap isn't split: the suite's largest cell, HellaSwag's 6,299 docs both runs got right, is about 2,000 px tall at phone width and twice the pixel density, under the 4,096 px some phones allow.

## 5. Known gaps

- `items()` still matches the two runs' rows once per cell; sharing that work across cells needs a change to eval, outside this stage.
- The map has no text: a reader opens a doc to see it.
