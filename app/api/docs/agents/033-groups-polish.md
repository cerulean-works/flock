# 033: run groups beside the eval pages

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-26 |
| Builds on | [026](026-run-groups.md) (D215 to D230: groups), [032](032-eval-runs-by-suite.md) (D210 to D214: the Evals preset) |
| Amends | [026](026-run-groups.md): D219 (suggestions), D221 (Compare's charts), D222 (a group's default view, Visualize's layout); [018](018-workspace-panels.md): sections share a row |
| Keeps | every route, stored setting and the assistant's requests |

## 1. Goal

Groups (026) were built on training runs, which log a loss curve. A project of eval runs, scored with `flock eval log`, logs suite scores as summaries only. Grouping those runs gave a group page with an empty chart pane and hash columns, a Compare page with half its charts empty, and suite names and numbers written unlike the eval pages. A few other rough spots showed on the same pass: a W&B group offered as a suggestion beside the saved group built from it, two Suggested columns both named `lr`, and three looks for one **Best by** button.

**Out of scope:** changes to the Runs page, the assistant's requests and stored settings, and items across a group of runs.

---

## 2. Design decisions

### D231. A group of eval runs opens on the Evals preset

When some run of a group has a suite score, its page opens on **Table** with the Evals preset (D210) on, instead of **Visualize**: its runs ranked by suites, read from the group's own snapshot. **Evals** turns it off for the visit (`?preset=off`). Other groups keep **Visualize** (D222). The check reads the page's runs, not the column list, which is the project's: a group of training runs in a project that also holds eval runs stays on **Visualize**.

### D232. Compare drops the history chart for a summary-only metric

When the snapshot of the rows' best runs has no history for the best-by metric (an eval suite's score is a summary only), Compare shows only the best values, across the page's width. Before, half the chart area said the metric had no history.

### D233. Suites are named and written as on the eval pages

A suite's summary column `summary.suites/zh_tw:bpb` is labelled `zh_tw:bpb` everywhere in groups. The history key keeps its `suites/` prefix (`chartKey`), so a run that logs a suite over steps still charts it. On Compare, a suite chosen as **Best by** takes the scale the Evals preset reads from the best runs (D211): an accuracy is written as `62.4%`, its change and its spread across repeats in points, any other scale to three decimals. Other metrics, and a row a group ranks by another metric, keep the runs table's format.

### D234. A W&B group a saved group filters on isn't suggested again

D219 suggested W&B groups from the runs in no saved group. Runs logged later into a W&B group that a saved group was built from are that group's new matches (D216), so they were offered twice: as new matches and as a suggestion named after the same W&B group. Now a W&B group named by a saved group's filter, `{"group": name}` as a suggestion saves it or `{"group": {"$eq": name}}` as the filter popover does, makes no suggestion, and its runs stay claimed so the config clusters don't take them. The suggestions' cache key gains the groups' last update, so a changed filter is read.

### D235. Visualize can stack the table above the charts

Side by side suits many runs and few charts; a group's few runs read better in a short table above charts that span the page. Selecting **Visualize** while it is on flips between the two; a small icon in the tab (columns or rows) shows which, and its hint says a second click flips it. The choice is the viewer's, kept in a cookie (`flock_visualize_stacked`) like the dragged widths, so the server renders it with no jump, and it holds on the Runs page and a group's alike. Side by side stays the default. Stacked, the divider is a horizontal bar that resizes the table's height (`TABLE_H`, 120 to 1200 px, 320 at first), and `ResizeHandle` gains a vertical axis for it.

### D236. Small sections share a row

A section with fewer panels than a row fits takes only their columns, so small sections sit side by side: a group logging `lr`, `train/loss` and `val/loss` shows `charts | train | val` in one row of three instead of three rows of one. A section with a row or more of panels spans the row as before, and a folded one takes one column. The sections flow on the panels' own column grid (up to four, none under 340 px), so a packed section's panels line up with a full one's, and a narrow pane (beside the table) still shows one section per row. Headers stay with their sections; a packed header has no edge-to-edge bleed.

### D237. Smaller fixes

- **Best by** is a trophy and the metric on every page (Compare, a group, the editor), as on a group's toolbar before; "Best by" is its tooltip and screen-reader text. The Evals preset's button (D210) takes a clipboard with a check instead of its trophy, so the trophy means only the best.
- Among Suggested columns, which have no group band, a metric with a setting's name is headed by its key (`summary.lr` beside `lr`), on Compare and in a group's table.
- The editor lists a saved group's runs and its new matches before the other runs (new matches first when opened to review them), by what was saved, so a row doesn't move when ticked.
- With no OpenAI key on the server, the assistant's settings are disabled under the note that says so.
- An empty draft has no template name ("0 runs"): the title shows the placeholder **New group**.

---

## 3. Plan

1. The trophy look, suite labels and the history key; the Evals button's own icon.
2. Compare: the suite's scale, then no history chart for a summary-only metric.
3. Suggested headers, the editor's order, the settings without a key, the empty name.
4. A group of eval runs on the Evals preset.
5. Suggestions: the saved W&B groups, with a test.
6. Docs: the groups guide.
7. Visualize stacked, flipped by a second click.
8. Small sections share a row.

## 4. How it was actually built

- `evalRuns` (`evals-view.svelte.ts`) reads the preset's runs from any workspace snapshot; the Evals preset, Compare (for a suite's scale) and a group's page (to tell an eval group) use it.
- `RunView` gains `evalsFirst`: the preset is on until the viewer turns it off, and the group page passes **Table** as its initial view, so a link with no view opens where the preset shows.
- `suggest` takes `taken`, the W&B group names saved groups filter on. A test covers both filter forms, the later runs as new matches, and another W&B group still suggested.
- Unit tests cover `metricLabel` on a suite, `chartKey` and the chart metrics of a suite.

## 5. Known gaps

- Sections in one row reorder by keyboard (a section's grip and the arrow keys), not by dragging sideways: a drag still moves a section by height, past the whole row.

- A suite's scale on Compare comes from the best runs' snapshot, read once the page has loaded; until then, and for a suite whose scale is unknown, scores show in the plain number format.
- A group with both eval and training runs opens on the preset, which lists only its eval runs; **Evals** shows the rest. Whether a group is one of eval runs is read from the page of runs on screen, so in such a mixed group a search or a page without eval runs opens **Visualize** instead.
- A group's plain table (with **Evals** off) still heads a suite's column `suites/zh_tw:bpb`, as the Runs page does.
