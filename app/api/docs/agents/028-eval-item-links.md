# 028: eval item links, keys and rows before text

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-23 |
| Builds on | [027](027-eval-compare-items.md) (D193 the items route, D195 the compare page, D198 rows with margins) |
| Amends | D193 (the list answers rows; a doc's `Item` comes from its own route, D199), D195 (the list's state is in the URL, D200; the counts table opens a task's items, D202), D198 (one open row, D201; the shared lines aren't said above the rows, D199) |

## 1. Goal

027 put a task's docs on the compare page and the Results tab, but a reader couldn't point a teammate at one: the task, the group, the page and the open doc lived only in the page's state. Moving through docs took the mouse, row by row. And each page sent every doc's whole text and every choice's numbers, though a reader skims rows and opens a few.

1. **Links.** The URL holds the list's state, so a pasted link reopens the same doc, for whoever may read it.
2. **Keys.** j and k move through the docs, turning pages at the ends; Enter opens, Esc closes.
3. **Rows first.** The list sends a short summary per doc; a doc's whole text and per-choice numbers come when it opens.

**Out of scope:** groups of more than two runs, per-token views, any change to how eval reads or scores.

---

## 2. Design decisions

### D199. The list answers rows; a doc's whole item comes from its own route

Inspect's log viewer reads sample summaries first, with long strings truncated, and a sample's full record when it's opened (`EvalSampleSummary`, `read_eval_log_sample`). The list here does the same.

- `GET .../runs/{run}/items` answers `{"total", "offset", "items"}`, each item a row: `id`, `text` (the part of the question that varies), `gold`, `gold_text`, and per run `correct`, `pick`, `pick_text`, `margin` and `gold_prob` (the gold choice's probability). The texts are clipped at 240 characters, about two lines of a wide column.
- `GET .../runs/{run}/item?doc=&task=&keep=[&with=]` answers that doc's `Item` as `flock eval items --json` prints it, so D193 holds for what a reader opens. The id is a query value, not a path segment, so an id such as `..`, which a browser would resolve away in a path, or one holding a slash, reaches it as written. It takes the list's `keep` because eval reads two runs by a cell of their diff; a doc outside that cell is a 404. Its access and refusals are the list's.
- **The margin is computed once, on the server**, where the sort already computed it (D198), so the page no longer carries a second copy of the formula.
- **The shared lines** a row leaves out (D198) are found over the whole filter, not one page, so a row reads the same on any page. None when every question is the same or a row would be left with nothing. They are no longer said above the table: a reader at the first look found the line noise, and an opened doc shows its question whole.
- **027's 16 MiB page budget is gone.** Its reason was each answer repeating its run's model name; rows carry no model name and their texts are clipped, so a page of 100 rows stays small. One item is bounded by the files `PairFiles` lets in.
- The cost of a page or a doc is the same: reading the task's files. Measured with eval's reader on two runs of a whole bank, HellaSwag's both-wrong cell (2,829 docs) takes 0.46 s to read; one full item is 1.6 KB.

### D200. The URL holds the list's state, written by the page from the reader's actions

Neuronpedia's graphs and Inspect's viewer both link to one item; a link here reopens a doc.

- **Params:** `task`, `keep`, `sort` (only `doc`, the non-default), `page` (from 2) and `item`, beside the page's own (`with` on the compare page, `tab` on the run page). A value at its default is left out, so a plain link stays short.
- **Read once,** when the list mounts. A part that doesn't fit is ignored: a task the runs don't share, a keep of the other number of runs, a page that isn't a whole number. A page past the last (the docs changed since) shows the last.
- **Written by the page, from events.** The list calls `onview` after each change the reader makes (a filter, the order, a page, a row opened or closed, a key), untracked, and the page writes the URL with `replaceState` (shallow, so no load runs). 027 hit a loop when an effect both read and wrote `page.url` (D195); here nothing reads the URL after mount. The component never touches the URL itself, so its tests don't either.
- **Replace, not push:** moving through docs doesn't fill the browser's history, as the run page's tabs don't.
- **Access:** the doc route checks the viewer as the list does, so a link to a private task's doc shows its text to the org's members only. To anyone else it's eval's refusal, with no text.

### D201. j and k, Enter, Esc; one open doc

Our own choice, at the dashboard's first look: j and k move a reader through a list in mail and code-review tools, and leave the arrow keys to scroll.

- **j and k** move the focus to the next or previous doc of the filter; past a page's end they turn the page and land on its first (or last) doc. With a doc open they open the next one instead, so a reader reads doc after doc.
- **Enter** (the row's button) or **o** opens the focused doc, **Esc** closes the open one and leaves the focus on its row.
- **One doc open at a time** (027 let several open), so a link names one and j has one place to move from.
- Keys typed in a field, a select, a menu or a dialog are theirs. The compare page's A and B keys stay.
- A footer under the list names the keys, as the pickers' footers do.

### D202. The counts table shows each run's accuracy and opens a task's items

- Beside the docs and four counts, each task's row shows A's and B's accuracy on the docs compared (both right plus right in that run alone, over the docs) and A's lead in points; the footer does the same over all compared docs. Counts alone made a reader add up which run did better.
- Clicking a task (its row, or its name as a button from the keys) opens its items above: the link takes the task, keeps the reader's group and order when the task has docs in that group, else the first group with docs, and the list starts over there.

---

## 3. Plan

1. The routes (`app/api/src/api/runs.py`): rows, the doc route, tests.
2. The relay for a doc, the types, `itemsViewOf` and `withItemsView` in `evals.ts`, `EvalItems` with keys and the open doc, and its two callers.
3. Docs: the dashboard guide and the API reference.

## 4. How it was actually built

- `run_items` computes each item's margins once and sorts on them; the rows read the same list. The clip is a local function of the route.
- `run_item` calls `items()` for the list's cell and picks the doc by id.
- `EvalItems` keeps the open id and its fetched item; the item route is asked when a doc opens and dropped on any change. A `landing` names the page j or k turned to and where they were going on it; the answer for any other page drops it.
- The pager is the dataset Rows tab's, lifted into `PageNumbers` for both. Its Previous and Next take an optional step, so a dataset opened at any row still moves a page's length from it.
- The keys' guard (a key typed in a field, select, menu or dialog is its own) is `typedIn` in `evals.ts`, shared with the compare page's A and B.
- The run page passes its URL's view through `RunResults`; the compare page passes it straight to `EvalItems`.

## 5. Known gaps

- Each page and each opened doc re-reads the task's files; a large task costs that read every time (027's gap, now per doc too). No cache was added: none measured slow enough.
- The margin is still rebuilt from shares, floored at 1e-300 (027's gap), now in one place.
