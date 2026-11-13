# 018: custom panels, smoothing, loss forecasts and a quieter workspace

| | |
| --- | --- |
| Status | applied |
| Date | 2026-11-14 |
| Builds on | [001](001-wandb-compatible-flock.md) |
| Keeps | everything before D129 |

## 1. Goal

The project workspace already draws W&B's line plots, keeps a panel's settings, and remembers
hidden runs, panel order and sizes. This stage closes the gaps that still send people back to
W&B:

1. New runs show up on their own, without a "1 new run · Show" button (D129).
2. A viewer can add their own line plot to the workspace (D130, D131).
3. The workspace's chart toggles are remembered per project, and a project opened for the
   first time shows only its 10 newest runs (D132, D133).
4. Smoothing offers W&B's five choices instead of one EMA slider (D134).
5. A panel can forecast where a run's loss is heading, from a fitted curve (D135, D136).
6. The panel editor is one form, not a Data tab and a Display preferences tab (D137).

All of it lives in the web app (`app/web`). Nothing changes in the API or the stored data on
the server. Settings stay in the viewer's browser like the rest of the layout.

## 2. Design decisions

### D129. New runs, removed runs and new metrics apply themselves

Today `live.ts` counts runs created since the page was read (`ws.newRuns`), and the header
offers a button that reloads the page of runs. Instead, when any of the three counts goes above
zero the workspace reloads the page itself, the same way the button does (`clearRunPages` then
`refreshAll`). Reloads are debounced by 2 seconds, so a sweep launching 30 runs reloads once
or twice, not 30 times. The button and `updatesLabel` go away.

The page isn't reloaded while the viewer is in the middle of something that a reload would
disturb: dragging a panel or a column, a selection in the table, or an open dialog. The reload
waits until that ends. Rows shifting under the pointer is accepted, as it is in W&B.

### D130. "Add panel" opens the panel editor for a new line plot

The charts toolbar gets an **Add panel** button. It opens the existing panel editor
(`PanelEditor.svelte`) with an empty line plot. There is no picker of panel kinds. The viewer
chooses metrics (any number, from the history stream), and every other setting the editor
already has (x axis, ranges, labels, smoothing, forecast, legend, log scale). **Create** adds
the panel. An empty metric list can't be created, using the existing "Plot at least one
metric" error.

A custom panel has the usual panel menu, plus **Delete panel**. Built-in panels can't be
deleted, as today.

### D131. Custom panels live in a "Custom panels" section at the top

Custom panels go in their own section, first in the workspace, so they're easy to find. The
section can be reordered, resized and paged like any other. It only exists while it has a
panel.

They are kept in the layout (`PanelLayout`) as `custom: { id, config }[]`, where `config` is
the same `PanelConfig` and `config.metrics` is required. Their ids start with `custom:`, so
they can't collide with a metric's panel. A custom panel whose metrics no longer exist in the
project still shows, empty, so it isn't lost when a metric is renamed.

### D132. The workspace's chart toggles are remembered per project

The workspace-wide settings (smoothing, show original, ignore outliers, x axis, rows per
section) are plain `$state` today and reset on every visit. They are saved with the layout in
`localStorage` under the project, and read back in `restoreLayout`. Hidden runs, panel order,
sizes and per-panel settings are already remembered and don't change.

### D133. A project with no saved state shows its 10 newest runs

When `restoreLayout` finds no saved hidden runs for a project (the key is missing, not empty),
it hides every run on the page except the 10 newest by creation time, and saves that. From then
on it is ordinary saved state: the viewer can show or hide anything, and runs created later are
shown when they arrive (D129).

Runs on later table pages aren't hidden by this, since they aren't loaded. Charts only plot the
current page, so this only matters if the viewer pages through. Showing all runs of a page
again is one click on the existing "show all" toggle.

### D134. Five smoothing methods, like W&B

Smoothing becomes a method and a parameter, everywhere it is set today (the workspace's line
plot settings and a panel's editor).

| Method | Parameter | Notes |
| --- | --- | --- |
| No smoothing | none | |
| Exponential moving average | weight, 0 to 0.99 | Today's debiased EMA, unchanged. The default for old saved settings. |
| Time weighted EMA | weight, 0 to 0.99 | EMA whose decay scales with the gap between steps, so unevenly logged runs smooth the same as even ones. |
| Running average | window, 1 to 100 points | Mean of the last `window` finite points. |
| Gaussian | sigma, 0.1 to 100 steps | Centered Gaussian kernel over x, truncated at 3 sigma. |

`PanelConfig.smoothing` (a number) stays readable, as an EMA weight. New settings are saved as
`smoothingType` plus `smoothing`. The memoised cache in `series.ts` keys on both. All four
methods skip non-finite points the way `ema` does now.

### D135. A forecast extends a run's loss with a fitted power law

A panel can show a **forecast**: a dashed continuation of each visible run's line past its last
logged step. The fit is a saturating power law, the usual shape of a training loss:

```
L(t) = a + b · t^(-c),   b > 0, c > 0
```

It is fitted to the raw points (not the smoothed ones) after the first 10% of the run, which
skips warmup. `a` is searched on a grid below the run's minimum, and for each `a`, `b` and `c`
come from a least squares line through `log(L - a)` against `log t`. The `a` with the lowest
error wins. This is cheap, has no dependencies and is deterministic. A run with fewer than 20
points, or whose fit comes out increasing, gets no forecast.

The forecast reads out in the tooltip as "Forecast" next to the run's value, and isn't counted
in the y axis's outlier trimming.

### D136. Forecasts are a per-panel toggle with a horizon

The panel editor gets **Forecast** (off by default) and **Forecast to step** (blank means twice
the longest run's last step). The workspace's line plot settings get the same toggle, which
panels follow unless they set their own, like smoothing. Forecasts only apply to history
metrics, not system metrics.

### D137. The panel editor is one form

The Data and Display preferences tabs are dropped. The editor shows one scrolling form with
plain section headings: **Data** (metrics, x axis, ranges), **Smoothing**, **Forecast** and
**Display** (title, labels, legend, line width, log scale). The preview stays beside it.

## 3. Plan

One branch (`feat/workspace-panels`) and one PR, built in this order, each with unit tests next
to the existing ones (`series.spec.ts`, `panels.spec.ts`, `layout.spec.ts`):

1. D137, the single-form editor, since D130, D134 and D136 all add to it.
2. D134, smoothing methods, in `series.ts` and the settings UI.
3. D132 and D133, remembered toggles and the 10-run default.
4. D129, live runs without the button.
5. D130 and D131, custom panels.
6. D135 and D136, forecasts.

## 4. Known gaps after this stage

- Only line plots can be added. Bar charts, scatter plots and the rest of W&B's panel list are
  out of scope.
- Settings are still per browser. A viewer on another machine starts from the defaults.
- The forecast is one curve shape. A run with a learning rate schedule that drops late (step
  decay) will be forecast poorly before the drop.

## 5. How it was actually built

Built in the planned order on `feat/workspace-panels`, one commit per step.

- **Smoothing (D134).** The methods live in `chart/series.ts` (`smooth`, `SMOOTHING_METHODS`).
  The time weighted EMA decays by `weight` over the run's average gap between finite points,
  so on evenly logged steps it equals the EMA. The Gaussian's sigma is in the units of the x
  axis the panel plots on, which are steps unless the panel uses another axis. The workspace
  default stays an EMA of weight 0. One `SmoothingPicker` serves the workspace settings and the
  editor, where a "Workspace" method follows the workspace, in place of the old switch.
- **Settings (D132).** They are kept in the layout as `settings`, next to the panels, and read
  through getters on `Workspace`, so the controls bind to them as before. A run's page shares
  them, as it shares the layout.
- **Newest runs (D133).** "Newest" is the run's `ordinal`, which Flock numbers by creation time.
  A saved value that isn't a list (missing, or unreadable) counts as no saved state.
- **Live runs (D129).** The page is read again 2 seconds after the first change, not 2 seconds
  after the last, so a steady stream of new runs still reloads. While the viewer is busy it
  checks again every 2 seconds. Open menus and popovers (such as the columns menu, where columns
  are dragged) count as busy too.
- **Custom panels (D130, D131).** `Workspace.panelDefs` is the page's panels followed by the
  added ones, so added panels are numbered like any other and keep pinning, order, height,
  rows and zoom. Their section key is "Custom panels". It goes first unless the viewer moved it.
  A run's page shows them too. Delete panel takes effect at once, with no confirmation. Reset
  to defaults keeps an added panel's metrics.
- **Forecasts (D135, D136).** The fit is in `chart/forecast.ts`. After a grid of 48 asymptotes
  it refines the best one with a golden section search, so a clean power law is recovered
  closely. The forecast starts at the fitted value at the run's last step, which can sit a
  little off the drawn line. Forecasts are skipped on wall time x axes, where a power law in
  epoch seconds means nothing, and their horizon is in the units of the panel's x axis.
  Forecasts count toward the y range unless outliers are ignored.
- **Not checked by hand.** The UI was not tried in a browser. Checks, lint, unit tests and the
  build pass.
