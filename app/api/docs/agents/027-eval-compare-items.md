# 027: eval items with their text in the dashboard

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-22 |
| Builds on | [025](025-eval-results.md) (D184 the Results tab, D185 the item comparison, `PairFiles`, `members_only`); eval's stage 043 (`flock/eval/docs/agents/043-item-text.md`: schema 11 predictions hold each doc's text and pick, `flock.eval.items.items` and its `Item` JSON) |
| Keeps | D1 to D192 |

## 1. Goal

025's comparison page counts, per task, the docs both runs got right, both got wrong and only one got right, and lists ids. An id doesn't say why a model failed: a person has to find the doc in the dataset and guess what each model preferred. From results schema 11 a run's predictions hold each doc's question, choices and pick (eval's stage 043), so the dashboard can show the behaviour behind a count:

1. **Compare items.** The comparison's main content becomes the docs themselves, two runs side by side: the question and choices, the gold choice, each run's pick, and each run's share of every choice. The four counts and the task are filters above the list.
2. **One run's items.** A run's Results tab gets the same list for that run alone, showing its wrong docs by default.
3. **One route** returns a page of either, as `flock eval items --json` prints it, so the page, the CLI and an agent reading the API see the same items.

**Out of scope:** groups of more than two runs (the item JSON holds a list of runs per doc, so a group fits it later); perplexity tasks, whose documents are whole texts with no choices; launching; any change to how eval scores.

---

## 2. Design decisions

The numbering continues from 026 (eval launches, D188 to D192), which merges before this stage; if it doesn't, renumber these from D188.

### D193. Items are eval's own, read on the server and returned unchanged

- `GET .../runs/{run}/items?task=&keep=[&with=]` calls `flock.eval.items.items` on the runs' files and returns its `Item` objects as `to_dict` writes them, in a page: `{"total", "offset", "items"}`. The same reader as `flock eval items`, so the dashboard and the CLI never disagree, as D185 has it for the diff.
- `keep` is eval's: one run's `wrong`, `right` or `all`, or a cell of two runs' diff. An unknown value is a 422 from the route's type; a value that doesn't fit the number of runs is eval's refusal, a 409.
- **Paging** is a slice of the task's kept items, 20 by default and at most 100. `items()` takes a `limit` but no offset, and a page needs `total` anyway, so the route builds the task's items and slices them. One task's two predictions files are read per page, within `PairFiles`' budget, so a page costs what one task of the diff costs. Caching the parsed files between pages was left out: no page measured slow enough to need it.
- **Refusals** are the diff's: 404 for a run that's unknown, unreadable or without `results.json`; 409 with eval's reason for anything `items()` refuses, a run before schema 11 among them ("score the model again to read its items"). The page shows that reason as it is.

### D194. A private dataset's text reaches the org's members only, through the diff's check

- The diff and the items read a run's files through one function, `eval_readers`, which applies `members_only` (D181) per run, as 025's diff did inline. A task on a private dataset has no predictions to an outsider or an anonymous viewer of a public project, so `items()` refuses it with "has no predictions" and no text leaves. A member, an admin and a super admin read it.
- Moving the check into one function shared by both routes keeps one access rule for counts and text, rather than two copies that could drift.

### D195. The page: items first, with the task and the cells as filters above them

No external precedent is claimed for this layout. The aim is to answer "why did this model get it wrong" from the page alone: the question sits beside both runs' answers, and a reader sees at a glance which choice each run took. How an item reads is D198.

- **Order.** The pickers, then the items, then 025's table of counts per task, then the tasks not compared. The ids 025 listed under each task are gone from the table: the items replace them, and a run without text keeps them (D197).
- **Filters.** A task select listing each task with the current cell's count, and the four cells (only A right, only B right, both wrong, both right) as a radio group with their counts from the diff. The first task with docs in the first cell is chosen by default, and choosing another pair starts over. The choice isn't written to the URL. A comparison is opened by its pair, which the URL holds, and read by moving through cells and pages; nobody has asked to share a page of items yet. When someone does, the pages own `task`, `keep` and `page` in their URL and pass them in.
- **Paging.** Previous and next with "1 to 20 of N", 20 items a page; not one long page, since a cell can hold thousands of docs.

### D196. A run's Results tab lists its own items, its wrong ones by default

- The same component, one column, with `wrong` (the default), `right` and `all` and their counts. A run before schema 11 gets one line saying its predictions hold no text and to score the model again, with no request: the tab already knows the schema. It offers the tasks whose primary metric is an accuracy (eval's `acc_*`), the ones `items()` reads.
- The counts come from the results file without reading predictions: docs right are the accuracy times the docs scored, rounded, which is exact for an accuracy over those docs.

### D197. A run without text keeps 025's view, with eval's reason

- For runs before schema 11, Flock answers 409 with eval's message, which names the schema and says to score the model again. The page shows it as the reason there is no text, and under it the ids of the docs only one run got right for the chosen task, as 025 showed them, with **Copy ids**. A both-right or both-wrong cell has counts only, as before.

### D198. Rows with a signed margin, the most telling first

Cards with every choice listed made a page of 20 docs long to scan and hid how sure each run was; one row per doc with a number for that certainty lets a reader skim to the docs that matter.

- **A run's margin** is its gold choice's score minus its best wrong choice's, in the primary metric's own units, so its sign always agrees with right or wrong (a raw log-probability margin can disagree under a length-normalized metric). It needs no new data: `share` is a softmax of the metric's scores, so the log of a ratio of shares is a gap between scores. A share that underflowed to zero is floored at 1e-300. The scorer picked in FP32 and the share is rebuilt in double precision, so a margin that rounds to zero can show the other sign than the outcome; the page says so. `acc_greedy` ranks no choices and a one-choice doc has no wrong one: no margin.
- **One row per doc**: the id, the part of the question that varies, the gold choice, and per run its pick after a check or a cross (the shapes differ, so colour isn't needed; a screen reader hears right or wrong), the signed margin tinted by sign and by its size against the page's widest, and the pick; with two runs, the gap. Right or wrong alone repeats the filter; the margin says how sure each run was, so a coin flip and a real disagreement look different. A row opens to the whole question and the per-choice table with shares, probabilities and log-probabilities, now always shown there rather than on hover.
- **Order.** `sort=margin` (the default) puts, for two runs, the biggest gap first and, for one run, the surest docs first, so the first page holds the items most likely to say something; `sort=doc` keeps the dataset's order. The sort runs on the server over the whole filter, so page one is the most telling of the task, not of the first 20 docs. Items without a margin go last, in doc order.
- **The shared wrapper.** The whole lines every question of the page shares at both ends is said once above the table, and rows show what's between. Eval's question already leaves out the few-shot examples (they're in the predictions file's header), so this mostly removes `Question:` and `Answer:`.
- **Score and churn.** Above the table, each run's accuracy on the task, the difference in points and the flips both ways, from the diff's counts: a net gain of a few points can hide many docs flipping each way.
- **Opening a row** grows it in place rather than showing a separate panel: the question cell holds the whole question, and a row per choice follows in the same columns (the choice under Question, the gold mark under Gold, each run's share bar and numbers under its column), so a run's numbers sit beside the choice they're for. The pick has a solid bar and darker numbers, the other choices lighter ones; right or wrong stays with the row's icon above, so the opened lines carry no second icon. Each choice is a row header, and each run's cell is named with its share, probability and log-probability, so a screen reader hears the numbers without a hover. While a row is open, the closed rows of the page fade and come back on hover or focus, so the open one reads on its own.
- **Columns** resize by dragging, with the runs table's `ResizeHandle` and widths kept in cookies (`ITEM_W` in `widths.ts`) as the sidebar's and the name column's are; both run columns share one width so they stay side by side. The initial widths fit two runs in Home's 70rem column. On the compare page the table's box uses the `bleed` utility (as the dataset rows do), padded so the table starts under the text: a table widened past the text grows toward the window's edge, then scrolls sideways, as a wide table does in a notes app. The Results tab is already full width and scrolls in its own pane.
- **Ready for more per-choice detail.** An opened choice is its own component (`ItemChoiceRow`), so values per token can join the share and numbers there without touching the table; the page treats an item's runs as a list and reads only the fields it names, so a later row or item field doesn't break it.
- **One row format** for every task with choices; a task type that needs another view (generation, per-token losses) plugs in its own opened view later, and N runs become more columns.

---

## 3. Plan

1. The route and the shared reader setup (`app/api/src/api/runs.py`), with tests for paging, the four cells, refusals and access.
2. The relay (`app/web/src/routes/api/projects/[entity]/[project]/runs/[run]/items/+server.ts`), the item types, the `EvalItems` component, and its two callers: the compare page and the Results tab.
3. Docs: the dashboard guide and the API reference.

## 4. How it was actually built

- `eval_readers` in `runs.py` returns the run (and the other run, given) as eval's `run:` sources with a reader over `PairFiles` that applies `members_only`. `diff_runs` and `run_items` both use it; `diff_runs` is otherwise unchanged.
- `run_items` runs in the diff's thread pool (`DIFFS`) for the same reason: each file read waits on the event loop.
- The page's JSON is encoded in that thread too, one item at a time, and held to 16 MiB (`PAGE_BYTES`), a 409 past it: each answer repeats its run's model name from `results.json`, so a page could otherwise grow far past the files `PairFiles` let in.
- Tests (`tests/app/test_evals.py`): the route's items equal `items()` on the same folders for every keep, with paging and `total`; refusals (schema 10, a keep for the other number of runs, an unknown task, an unknown keep, a limit over 100, a run without `results.json`, malformed or unknown `with`); a public project with a private task read by the admin, a member, an outsider and an anonymous viewer.
- The web relays the route with `relayAnswer`, as the dataset routes do, so a refusal (eval's 409, or a 422 for a bad query) reaches the page as `{message}`; the page names each run as its column does instead of eval's `run:` path.
- `EvalItems.svelte` is shared by the compare page and the Results tab. The Results tab keys it by entity, project and run, since run ids are per project.
- The first version showed each doc as a card with every choice and each run's share on hover. After a first look it became D198's rows with margins: the cards were slow to scan and said nothing of how sure a run was.
- The margin sort (`sort=margin`) ranks each item by a key over its runs' margins and sorts stably, so items without a margin keep doc order at the end. The page computes the same margins from the same shares to show them; the route returns eval's JSON unchanged, so the formula sits in both, each with a test of its numbers.
- Column widths are read from their cookies in the browser, since the items are fetched there; the sidebar's and the name column's are read by the server.

## 5. Known gaps

- Each page re-reads the task's files; a task with large predictions (HellaSwag's 10,042 docs) costs that read per page.
- An opened item draws a row per choice; a task with many choices (none in Base Easy has more than five) would need another layout.
- A share that underflowed is floored at 1e-300, so two margins past about 690 in the metric's units read alike. Eval's JSON holds shares, not the ranking scores; a margin field in eval's items would remove the floor and the second copy of the formula.
