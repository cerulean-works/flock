import { createContext } from 'svelte';
import { SvelteMap, SvelteSet } from 'svelte/reactivity';
import {
	DEFAULT_X_AXIS,
	mapAt,
	remapped,
	resolveAxis,
	sharedStepMetric,
	type MapSource,
	unmapAt,
	type XAxis
} from '#lib/chart/axes.ts';
import { slotOf, type Series } from '#lib/chart/series.ts';
import {
	LIVE_STATES,
	type MetricStream,
	type ProjectMeta,
	type RunPage,
	type SectionDef
} from '#lib/types.ts';
import { toggle } from '#lib/utils.ts';
import {
	type ColumnGroup,
	type ColumnPrefs,
	LOCKED_COLUMNS,
	NO_COLUMN_PREFS,
	arrangeColumns,
	moveColumn,
	moveGroup,
	parseColumnPrefs
} from './columns.ts';
import {
	EMPTY_LAYOUT,
	type PanelConfig,
	type PanelLayout,
	arrange,
	clampRows,
	mergeOrder,
	migrateOrder,
	moveKey,
	parseLayout
} from './layout.ts';
import { resolvePanel, type PanelView } from './panels.ts';

export type { PanelConfig, PanelLayout } from './layout.ts';
export type { PanelView } from './panels.ts';

/**
 * Run colors. The first eight are a CVD-checked categorical order; the next eight are the same
 * hues one step deeper. A project has far more runs than any palette has distinguishable hues,
 * so colors repeat every 16 runs; identity is recovered through hover, the chart tooltips and the
 * run list rather than color alone. Color follows the run's position in the project, never its
 * rank on the current page, so sorting and paging never repaint a run.
 */
const PALETTE = [
	'#2a78d6',
	'#eb6834',
	'#1baf7a',
	'#eda100',
	'#e87ba4',
	'#008300',
	'#4a3aa7',
	'#e34948',
	'#1c5cab',
	'#b84a1c',
	'#127a55',
	'#a87300',
	'#b8487a',
	'#005c00',
	'#7d70d1',
	'#a83232'
];

const runColor = (ordinal: number) => PALETTE[ordinal % PALETTE.length];

/**
 * Color of the `k`th metric of a panel, when its lines are told apart by metric (one run, as on
 * a run's page): the run palette again, so a panel's first line keeps the first color.
 */
export const metricColor = (k: number) => PALETTE[k % PALETTE.length];

/** Where a plot is drawn: a grid card, the full-screen panel, or the panel editor's preview. */
export type Surface = 'grid' | 'fullscreen' | 'editor';

export interface Cursor {
	/**
	 * x under the pointer, in the units of `axis`, snapped to the nearest step a line of the
	 * panel logged. Shared so every panel on the same x axis draws the same crosshair and marks
	 * its own points there.
	 */
	step: number;
	/** How far from `step` (in its units) a point reads out: a few pixels of that panel. */
	tol: number;
	/** `XAxis.id` of the panel the pointer is in. */
	axis: string;
	/** Panel the pointer is in. */
	panel: number;
	/** That panel as drawn there (the editor previews unsaved settings). */
	view: PanelView;
	/** Which rendering of that panel owns the pointer. */
	surface: Surface;
	/** Pointer height in that panel's plot, as a share of it from the top: tooltips follow it. */
	fy: number;
	/** Page row of the run nearest the pointer, if any. */
	nearest: number | null;
	/** Metric of the line nearest the pointer, if any. */
	line: number | null;
	/**
	 * Per page row, the history row (`_step`) under the pointer, NaN where the run has none:
	 * set when the pointer is in a panel of logged metrics, so panels of logged metrics on other
	 * x axes (another step metric, `_step`, a time) follow it too, each run at its own x there.
	 */
	rows: Float64Array | null;
}

export type XRange = readonly [number, number];

/** A drag-zoom, in the units of the x axis it was made on. */
interface Zoom {
	axis: string;
	range: XRange;
}

/** Plot height of a panel in the grid, unless its section was resized. */
export const PLOT_H = 196;
export const MIN_PLOT_H = 120;
export const MAX_PLOT_H = 720;

const layoutKey = (entity: string, project: string) => `flock:panels:${entity}/${project}`;
const columnsKey = (entity: string, project: string) => `flock:columns:${entity}/${project}`;

/** A value this browser kept, or null when there's none (or storage is blocked or unreadable). */
function readStored(key: string): unknown {
	try {
		return JSON.parse(localStorage.getItem(key) ?? 'null');
	} catch {
		return null;
	}
}

function store(key: string, value: unknown) {
	try {
		localStorage.setItem(key, JSON.stringify(value));
	} catch {
		// Kept for this visit only.
	}
}

/** A change to some runs, asked for from a row's menu or for the selection. */
export interface RunDialog {
	kind: 'edit' | 'tag' | 'move' | 'delete';
	ids: string[];
}

/**
 * Client state for one workspace.
 *
 * Server data (`meta`, `page`) is read through getters so it always reflects the latest load
 * result and is never proxied. Everything here is UI state layered on top; collections are
 * Svelte's reactive Set/Map, so toggling one run only notifies readers of that run's id.
 *
 * Live updates mutate the page in place and then bump a version: per metric for curves (so
 * only panels of metrics that received points repaint) and one for the table.
 */
export class Workspace {
	#meta: () => ProjectMeta;
	#page: () => RunPage;

	/** Run ids hidden from charts. Kept across pages so a hidden run stays hidden. */
	readonly hidden = new SvelteSet<string>();
	/** Run ids selected in the table, for changing several at once. */
	readonly selected = new SvelteSet<string>();
	/** The open run dialog, if any. */
	dialog = $state.raw<RunDialog | null>(null);
	/**
	 * Runs this tab is deleting or moving away. The page reloads once that lands, so the
	 * stream's `deleted` event for them is not news.
	 */
	// eslint-disable-next-line svelte/prefer-svelte-reactivity -- bookkeeping, never rendered
	readonly removing = new Set<string>();
	/** Page row under the pointer (table row, run list, legend or nearest line). */
	hover = $state<number | null>(null);
	pinnedId = $state<string | null>(null);
	cursor = $state.raw<Cursor | null>(null);

	smoothing = $state(0);
	showOriginal = $state(true);
	ignoreOutliers = $state(false);
	/** The x axis of panels that don't set their own: one of `X_AXES`, or a metric key. */
	xAxis = $state(DEFAULT_X_AXIS);
	/** Bumped when live updates change a run's metric definitions. */
	defsVersion = $state(0);
	/** Rows of panels a section's page shows, unless the section was resized. */
	defaultRows = $state(2);
	/**
	 * A panel or section is being moved, or a section resized: plots don't follow the pointer
	 * (no crosshair, no hover) until it's let go.
	 */
	dragging = $state(false);
	/**
	 * Drag-zoom per panel, keyed by panel id: the panel list can differ between pages. The
	 * rest of a panel's settings are kept in its layout (`panelConfig`).
	 */
	readonly zoom = new SvelteMap<string, Zoom>();
	/** Id of the panel open in the panel editor. */
	editing = $state<string | null>(null);
	readonly collapsed = new SvelteSet<string>();
	/**
	 * Per section, the index of the first panel on the page shown (see `pageAt`): a page number
	 * would show other panels once pages hold more or fewer of them.
	 */
	sectionStart = $state.raw<Readonly<Record<string, number>>>({});
	/** Panel layouts by project, read from this browser once the page is showing. */
	layouts = $state.raw<Readonly<Record<string, PanelLayout>>>({});
	/** Table column settings by project, read from this browser once the page is showing. */
	columnsByProject = $state.raw<Readonly<Record<string, ColumnPrefs>>>({});

	/** Bumped when live points arrive for a metric. */
	readonly metricVersion = new SvelteMap<string, number>();
	/** Bumped when live updates change table cells or run states. */
	tableVersion = $state(0);
	/** Live connection to Flock. */
	connection = $state<'connecting' | 'live' | 'offline'>('connecting');
	/** Runs created since this page was loaded. */
	newRuns = $state(0);
	/** Runs deleted or moved away (elsewhere) since this page was loaded. */
	removedRuns = $state(0);
	/** Metrics first logged since this page was loaded (charted after a refresh). */
	newMetrics = $state(0);
	/** Live runs in the project (from the snapshot, then kept current by the stream). */
	activeRuns = $state(0);

	constructor(meta: () => ProjectMeta, page: () => RunPage) {
		this.#meta = meta;
		this.#page = page;
		this.activeRuns = page().active;
	}

	get meta() {
		return this.#meta();
	}

	get page() {
		return this.#page();
	}

	colors = $derived(this.page.runs.map((r) => runColor(r.ordinal)));

	/** Page rows plotted in charts, in table order. */
	visible = $derived.by(() => {
		const out: number[] = [];
		this.page.runs.forEach((r, i) => {
			if (!this.hidden.has(r.id)) out.push(i);
		});
		return out;
	});

	pinned = $derived.by(() => {
		if (this.pinnedId == null) return null;
		const i = this.page.runs.findIndex((r) => r.id === this.pinnedId);
		return i === -1 ? null : i;
	});

	/** The run that is emphasised everywhere: hovered, else pinned. Only visible runs focus. */
	focus = $derived.by(() => {
		const f = this.hover ?? this.pinned;
		return f != null && !this.hidden.has(this.page.runs[f]?.id) ? f : null;
	});

	/**
	 * Version of a panel's curves on this page (of every metric it plots); read it to repaint
	 * on live points. Versions only grow, so their sum changes whenever any of them does.
	 */
	version(view: PanelView) {
		let v = this.defsVersion;
		for (const m of view.metrics) v += this.metricVersion.get(this.meta.metrics[m].key) ?? 0;
		const map = this.axisOf(view).map;
		if (map) v += this.metricVersion.get(map) ?? 0;
		return v;
	}

	// ------------------------------------------------------------------------------------------
	// X axes (see #lib/chart/axes.ts)

	/**
	 * A logged metric's step metric, as the first of the page's runs to log and define the metric
	 * says (`sharedStepMetric`), provided every run that logged it logged that step metric too.
	 * Memoised until the page, a run's definitions or the metric's points change.
	 */
	#steps = $derived.by(() => {
		void this.defsVersion;
		const page = this.page;
		const defs = page.runs.map((r) => r.defs);
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a memo, never rendered
		const memo = new Map<string, { version: number; step: string | null }>();
		return (key: string) => {
			// Read on every call, so callers repaint when live points change who logged the metric.
			const version = this.metricVersion.get(key) ?? 0;
			const hit = memo.get(key);
			if (hit?.version === version) return hit.step;
			const m = this.metricOf('history', key);
			const step = sharedStepMetric(defs, key, {
				logged: (row) => m !== undefined && !!page.series[slotOf(page.metricCount, row, m)]?.n,
				maps: (row, step) => !!this.#mapping(row, 'history', step)?.n
			});
			memo.set(key, { version, step });
			return step;
		};
	});

	/** The step metric `key` is plotted against on "Step", if its runs defined one. */
	stepMetric(key: string): string | null {
		return this.#steps(key);
	}

	/** The x axis a panel plots on: its own choice, else the workspace's. */
	axisOf(view: PanelView): XAxis {
		const first = this.meta.metrics[view.metrics[0]];
		const step = view.stream === 'history' && first ? this.stepMetric(first.key) : null;
		return resolveAxis(view.config.xAxis ?? this.xAxis, view.stream, step);
	}

	/**
	 * What gives run `row`'s x values for mapping `key`: the run's mapping, else the curve of
	 * the metric of that name (a metric chosen as the x axis, or a step metric defined since
	 * the page loaded).
	 */
	#mapping(row: number, stream: PanelView['stream'], key: string): MapSource | null {
		const page = this.page;
		const axis = page.axes[row]?.[stream].get(key);
		if (axis?.n) return axis;
		const m = this.metricOf(stream, key);
		const own = m === undefined ? null : page.series[slotOf(page.metricCount, row, m)];
		return own?.n ? own : (axis ?? null);
	}

	/** When run `row` started, in epoch seconds: its first wall time, of either stream. */
	#startOf(row: number): number {
		const axes = this.page.axes[row];
		const a = axes?.history.get('_timestamp')?.first ?? NaN;
		const b = axes?.system.get('_timestamp')?.first ?? NaN;
		return Number.isNaN(a) ? b : Number.isNaN(b) ? a : Math.min(a, b);
	}

	/** Run `row`'s curve of metric `m` on `axis`, or null when it has none there. */
	lineSeries(row: number, m: number, axis: XAxis): Series | null {
		const page = this.page;
		const s = page.series[slotOf(page.metricCount, row, m)];
		if (!s || !axis.map) return s;
		const map = this.#mapping(row, this.meta.metrics[m].stream, axis.map);
		if (!map) return null;
		return remapped(s, axis.id, map, axis.relative ? -this.#startOf(row) : 0);
	}

	/**
	 * Run `row`'s history row (`_step`) at `x` on a logged-metric `axis`, or NaN where the run
	 * has no x values on it. What the crosshair of a panel tells panels on other x axes: a
	 * row means the same to every axis, whatever its step metric is called.
	 */
	historyStepAt(row: number, axis: XAxis, x: number): number {
		if (!axis.map) return x;
		const map = this.#mapping(row, 'history', axis.map);
		if (!map?.n) return NaN;
		return unmapAt(map, x, axis.relative ? -this.#startOf(row) : 0);
	}

	/** Run `row`'s x on a logged-metric `axis` at history row `step` (`historyStepAt` back). */
	xAtHistoryStep(row: number, axis: XAxis, step: number): number {
		if (!axis.map) return step;
		const map = this.#mapping(row, 'history', axis.map);
		if (!map?.n) return NaN;
		return mapAt(map, step, axis.relative ? -this.#startOf(row) : 0);
	}

	/** A run's current state; reactive to live updates. */
	stateOf(row: number) {
		void this.tableVersion;
		return this.page.runs[row].state;
	}

	/** A run's display name; reactive to live edits. */
	nameOf(row: number) {
		void this.tableVersion;
		return this.page.runs[row].name;
	}

	/** A run's value in a table column (`tags`, `config.lr`, ...), or undefined without one. */
	cell(row: number, key: string): unknown {
		void this.tableVersion;
		const c = this.meta.columns.findIndex((col) => col.key === key);
		if (c === -1) return undefined;
		const nums = this.page.num[c];
		return nums ? nums[row] : this.page.text[c]?.[row];
	}

	/** Page rows of the selected runs, in table order. */
	selectedRows = $derived.by(() => {
		const out: number[] = [];
		this.page.runs.forEach((r, i) => {
			if (this.selected.has(r.id)) out.push(i);
		});
		return out;
	});

	toggleSelected(row: number) {
		toggle(this.selected, this.page.runs[row].id);
	}

	/** Select every run on the page, or none if all are already selected. */
	toggleSelectAll() {
		const all = this.selectedRows.length === this.page.runs.length;
		this.selected.clear();
		if (!all) for (const r of this.page.runs) this.selected.add(r.id);
	}

	/** Open a run dialog for one row, or for the selection. */
	openDialog(kind: RunDialog['kind'], row?: number) {
		const ids =
			row == null ? this.selectedRows.map((r) => this.page.runs[r].id) : [this.page.runs[row].id];
		if (ids.length) this.dialog = { kind, ids };
	}

	/** Page rows of runs by id, skipping runs no longer on the page. */
	rowsOf(ids: readonly string[]) {
		const runs = this.page.runs;
		return ids.map((id) => runs.findIndex((r) => r.id === id)).filter((r) => r !== -1);
	}

	isLive(row: number) {
		return LIVE_STATES.has(this.stateOf(row));
	}

	toggleRun(row: number) {
		toggle(this.hidden, this.page.runs[row].id);
	}

	/** Show every run on the page, or hide every run if all are already shown. */
	toggleAll() {
		const ids = this.page.runs.map((r) => r.id);
		const allShown = ids.every((id) => !this.hidden.has(id));
		for (const id of ids) {
			if (allShown) this.hidden.add(id);
			else this.hidden.delete(id);
		}
	}

	togglePin(row: number) {
		const id = this.page.runs[row].id;
		this.pinnedId = this.pinnedId === id ? null : id;
	}

	// ------------------------------------------------------------------------------------------
	// Panels, as the viewer set them up.

	/** Metric index by stream and key; a system metric can share a logged metric's name. */
	#metricIndex = $derived.by(() => {
		/* eslint-disable svelte/prefer-svelte-reactivity -- rebuilt, never mutated */
		const out: Record<MetricStream, Map<string, number>> = {
			history: new Map(),
			system: new Map()
		};
		/* eslint-enable svelte/prefer-svelte-reactivity */
		this.meta.metrics.forEach((m, i) => out[m.stream].set(m.key, i));
		return out;
	});

	/**
	 * Panel id by the metric keys that used to name panels before system metrics were grouped
	 * (`system.gpu.0.gpu` → `system:gpu.gpu`): for saved orders and old full-screen links.
	 */
	#aliases = $derived.by(() => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- rebuilt, never mutated
		const out = new Map<string, string>();
		const metrics = this.meta.metrics;
		for (const p of this.meta.panels)
			for (const m of p.metrics) if (metrics[m].key !== p.id) out.set(metrics[m].key, p.id);
		return out;
	});

	/** Every panel of the page with its settings applied, as `meta.panels` orders them. */
	panels = $derived.by((): PanelView[] => {
		const configs = this.layout.panels;
		const metrics = this.meta.metrics;
		const index = this.#metricIndex;
		return this.meta.panels.map((p) =>
			resolvePanel(p, configs[p.id], metrics, (stream, key) => index[stream].get(key))
		);
	});

	/** Index of a panel by id (or by a metric key it plots, from older links); -1 for none. */
	findPanel(id: string) {
		const panels = this.meta.panels;
		const i = panels.findIndex((p) => p.id === id);
		if (i !== -1) return i;
		const alias = this.#aliases.get(id);
		return alias ? panels.findIndex((p) => p.id === alias) : -1;
	}

	/** Metric index of `key` in a stream, if this page has it. */
	metricOf(stream: MetricStream, key: string) {
		return this.#metricIndex[stream].get(key);
	}

	/**
	 * Whether a panel's lines are colored by metric rather than by run: when it plots several
	 * metrics of one run, as W&B does. With several runs, run colors stay (a run keeps its color
	 * everywhere), and the legend and tooltips name each line's metric.
	 */
	byMetric(view: PanelView) {
		return view.metrics.length > 1 && this.visible.length <= 1;
	}

	/** Color of a panel's line for run `row` and its `k`th metric. */
	lineColor(view: PanelView, row: number, k: number) {
		return this.byMetric(view) ? metricColor(k) : this.colors[row];
	}

	/** A panel's settings (empty without any). */
	panelConfig(panel: number): PanelConfig {
		return this.panels[panel].config;
	}

	/** Replace a panel's settings; empty or null goes back to its defaults. */
	setPanelConfig(panel: number, config: PanelConfig | null) {
		const id = this.meta.panels[panel].id;
		const next = { ...this.layout.panels };
		if (config && Object.keys(config).length) next[id] = config;
		else delete next[id];
		this.#saveLayout({ ...this.layout, panels: next });
	}

	/** Open a panel in the panel editor. */
	editPanel(panel: number) {
		this.cursor = null;
		this.editing = this.meta.panels[panel].id;
	}

	isLog(panel: number) {
		return this.panels[panel].config.log ?? false;
	}

	toggleLog(panel: number) {
		const { log, ...rest } = this.panelConfig(panel);
		this.setPanelConfig(panel, log ? rest : { ...rest, log: true });
	}

	/**
	 * The panel's drag-zoom on `axis` (by default the one it plots on), if the viewer zoomed
	 * in there. Switching axes leaves a zoom behind rather than misreading its units.
	 */
	zoomOf(panel: number, axis = this.axisOf(this.panels[panel]).id): XRange | null {
		const zoom = this.zoom.get(this.meta.panels[panel].id);
		return zoom?.axis === axis ? zoom.range : null;
	}

	/**
	 * Zoom a panel in on `axis` (by default the one it plots on); null goes back to its x range
	 * (set in its settings, else the data's).
	 */
	setZoom(panel: number, range: XRange | null, axis = this.axisOf(this.panels[panel]).id) {
		const id = this.meta.panels[panel].id;
		if (range) this.zoom.set(id, { axis, range });
		else this.zoom.delete(id);
	}

	/** This project's panel layout. */
	get layout(): PanelLayout {
		return this.layouts[layoutKey(this.meta.entity, this.meta.project)] ?? EMPTY_LAYOUT;
	}

	/** A section's saved panel order, in today's panel ids. */
	#orderOf(section: string): readonly string[] {
		const saved = this.layout.order[section];
		const aliases = this.#aliases;
		return saved && aliases.size ? migrateOrder(saved, (k) => aliases.get(k)) : (saved ?? []);
	}

	/** Sections in the viewer's order, with their panels in the viewer's order. */
	sections = $derived.by((): SectionDef[] => {
		const { order, sections } = this.layout;
		const panels = this.meta.panels;
		return arrange(this.meta.sections, (s) => s.key, sections).map((sec) =>
			order[sec.key]
				? { ...sec, panels: arrange(sec.panels, (p) => panels[p].id, this.#orderOf(sec.key)) }
				: sec
		);
	});

	/**
	 * Read this project's layout and column settings from the browser. Called after hydration,
	 * so the page the server rendered (in the default layout) is what hydrates.
	 */
	restoreLayout() {
		const { entity, project } = this.meta;
		const key = layoutKey(entity, project);
		if (!(key in this.layouts))
			this.layouts = { ...this.layouts, [key]: parseLayout(readStored(key)) };
		const cols = columnsKey(entity, project);
		if (!(cols in this.columnsByProject))
			this.columnsByProject = {
				...this.columnsByProject,
				[cols]: parseColumnPrefs(readStored(cols))
			};
	}

	#saveLayout(layout: PanelLayout, persist = true) {
		const key = layoutKey(this.meta.entity, this.meta.project);
		this.layouts = { ...this.layouts, [key]: layout };
		if (persist) store(key, layout);
	}

	/** Move a panel to `to`, an index into its section's (arranged) panels. */
	movePanel(section: string, panel: number, to: number) {
		const sec = this.sections.find((s) => s.key === section);
		if (!sec) return;
		const ids = sec.panels.map((p) => this.meta.panels[p].id);
		const from = sec.panels.indexOf(panel);
		const moved = moveKey(ids, from, to);
		if (from === -1 || moved[from] === ids[from]) return;
		// Saved in today's ids, so metric keys of an older layout are replaced as it's saved.
		this.#saveLayout({
			...this.layout,
			order: { ...this.layout.order, [section]: mergeOrder(this.#orderOf(section), moved) }
		});
	}

	/** Move a section to `to`, an index into the (arranged) sections. */
	moveSection(section: string, to: number) {
		const keys = this.sections.map((s) => s.key);
		const from = keys.indexOf(section);
		const moved = moveKey(keys, from, to);
		if (from === -1 || moved[from] === keys[from]) return;
		this.#saveLayout({ ...this.layout, sections: mergeOrder(this.layout.sections, moved) });
	}

	/** Plot height of a section's panels. */
	heightOf(section: string) {
		return this.layout.height[section] ?? PLOT_H;
	}

	/**
	 * Resize a section's panels; null goes back to the default height. `persist` false while
	 * a drag is still going: it's saved once, when the drag ends.
	 */
	setHeight(section: string, px: number | null, persist = true) {
		const next = { ...this.layout.height };
		if (px == null) delete next[section];
		else next[section] = Math.round(Math.max(MIN_PLOT_H, Math.min(MAX_PLOT_H, px)));
		this.#saveLayout({ ...this.layout, height: next }, persist);
	}

	/** Rows of panels a section's page shows (it may have fewer to show). */
	sectionRows(section: string) {
		return this.layout.rows[section] ?? this.defaultRows;
	}

	/**
	 * Show more or fewer rows of a section's panels; null goes back to the default. `persist`
	 * false while a drag is still going: it's saved once, when the drag ends.
	 */
	setSectionRows(section: string, rows: number | null, persist = true) {
		const next = { ...this.layout.rows };
		if (rows == null) delete next[section];
		else next[section] = clampRows(rows);
		this.#saveLayout({ ...this.layout, rows: next }, persist);
	}

	// ------------------------------------------------------------------------------------------
	// Table columns the viewer hid or moved, kept per project in this browser.

	/** This project's column settings. */
	columnPrefs = $derived(
		this.columnsByProject[columnsKey(this.meta.entity, this.meta.project)] ?? NO_COLUMN_PREFS
	);

	/** Keys of this project's hidden columns. */
	hiddenColumns = $derived.by((): ReadonlySet<string> => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- replaced, never mutated
		return new Set(this.columnPrefs.hidden);
	});

	/** Indices into `meta.columns` of every column, in the viewer's order. */
	arrangedColumns = $derived(arrangeColumns(this.meta.columns, this.columnPrefs));

	/** Whether the viewer's order differs from the default one. */
	columnsMoved = $derived.by(() => {
		const def = arrangeColumns(this.meta.columns, NO_COLUMN_PREFS);
		return this.arrangedColumns.some((c, i) => c !== def[i]);
	});

	/** Indices into `meta.columns` of the columns the table shows, in order. */
	shownColumns = $derived.by(() => {
		const hidden = this.hiddenColumns;
		return this.arrangedColumns.filter((i) => {
			const key = this.meta.columns[i].key;
			return !hidden.has(key) || LOCKED_COLUMNS.has(key);
		});
	});

	/** Show or hide some columns (the locked ones always show). */
	setColumnsHidden(keys: readonly string[], hide: boolean) {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a draft, saved as a list
		const next = new Set(this.hiddenColumns);
		for (const k of keys) {
			if (hide && !LOCKED_COLUMNS.has(k)) next.add(k);
			else next.delete(k);
		}
		this.#saveColumns({ ...this.columnPrefs, hidden: [...next] });
	}

	/** Show every column again. */
	showAllColumns() {
		this.#saveColumns({ ...this.columnPrefs, hidden: [] });
	}

	/**
	 * Move the child at `from` of a column folder (a group's id, or a folder's) to `to`, among
	 * the arranged columns. A folder moves with its columns; nothing leaves its folder.
	 */
	moveColumn(parent: string, from: number, to: number) {
		const arranged = this.arrangedColumns.map((i) => this.meta.columns[i]);
		const order = moveColumn(arranged, this.columnPrefs.order, parent, from, to);
		if (order) this.#saveColumns({ ...this.columnPrefs, order });
	}

	/** Move a column group to `to`, an index into the (arranged) groups. */
	moveColumnGroup(group: ColumnGroup, to: number) {
		const arranged = this.arrangedColumns.map((i) => this.meta.columns[i]);
		const groups = moveGroup(arranged, this.columnPrefs.groups, group, to);
		if (groups) this.#saveColumns({ ...this.columnPrefs, groups });
	}

	/** Put the columns back in their default order (hidden ones stay hidden). */
	resetColumnOrder() {
		this.#saveColumns({ ...this.columnPrefs, order: [], groups: [] });
	}

	#saveColumns(prefs: ColumnPrefs) {
		const key = columnsKey(this.meta.entity, this.meta.project);
		this.columnsByProject = { ...this.columnsByProject, [key]: prefs };
		store(key, prefs);
	}

	toggleSection(key: string) {
		toggle(this.collapsed, key);
	}

	/** Show the page of a section's panels that starts at panel `start`. */
	setSectionStart(key: string, start: number) {
		this.sectionStart = { ...this.sectionStart, [key]: start };
	}
}

export const [getWorkspace, setWorkspace] = createContext<Workspace>();
