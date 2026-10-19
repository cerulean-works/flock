import { createContext } from 'svelte';
import { SvelteMap, SvelteSet } from 'svelte/reactivity';
import { LIVE_STATES, type ProjectMeta, type RunPage, type SectionDef } from '#lib/types.ts';
import { toggle } from '#lib/utils.ts';
import { LOCKED_COLUMNS, parseHidden } from './columns.ts';
import {
	EMPTY_LAYOUT,
	type PanelLayout,
	arrange,
	mergeOrder,
	moveKey,
	parseLayout
} from './layout.ts';

export type { PanelLayout } from './layout.ts';

/**
 * Run colors. The first eight are a CVD-checked categorical order; the next eight are the same
 * hues one step deeper. A project has far more runs than any palette has distinguishable hues,
 * so colors repeat every 16 runs; identity is recovered through hover, the cursor panel and the
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

export interface Cursor {
	/**
	 * x under the pointer: a step, or runtime seconds for system metrics. Shared so every panel
	 * with the same kind of x axis draws the same crosshair.
	 */
	step: number;
	/** Metric of the panel the pointer is in. */
	metric: number;
	/** Which rendering of that panel owns the pointer (grid card or full screen). */
	surface: 'grid' | 'fullscreen';
	clientX: number;
	clientY: number;
	/** Page row of the run nearest the pointer, if any. */
	nearest: number | null;
}

export type XRange = readonly [number, number];

/** Plot height of a panel in the grid, unless its section was resized. */
export const PLOT_H = 196;
export const MIN_PLOT_H = 120;
export const MAX_PLOT_H = 720;

const layoutKey = (entity: string, project: string) => `flock:panels:${entity}/${project}`;
const columnsKey = (entity: string, project: string) => `flock:columns:${entity}/${project}`;
const NO_HIDDEN: readonly string[] = [];

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
	panelsPerPage = $state(6);
	/** Per-panel settings, keyed by metric name: the metric list can differ between pages. */
	readonly logScale = new SvelteSet<string>();
	readonly zoom = new SvelteMap<string, XRange>();
	readonly collapsed = new SvelteSet<string>();
	sectionPage = $state.raw<Readonly<Record<string, number>>>({});
	/** Panel layouts by project, read from this browser once the page is showing. */
	layouts = $state.raw<Readonly<Record<string, PanelLayout>>>({});
	/** Table columns hidden, by project, read from this browser once the page is showing. */
	hiddenByProject = $state.raw<Readonly<Record<string, readonly string[]>>>({});

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

	/** Version of a metric's curves on this page; read it to repaint on live points. */
	version(metric: number) {
		return this.metricVersion.get(this.meta.metrics[metric].key) ?? 0;
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

	isLog(metric: number) {
		return this.logScale.has(this.meta.metrics[metric].key);
	}

	toggleLog(metric: number) {
		toggle(this.logScale, this.meta.metrics[metric].key);
	}

	zoomOf(metric: number): XRange | null {
		return this.zoom.get(this.meta.metrics[metric].key) ?? null;
	}

	setZoom(metric: number, range: XRange | null) {
		const key = this.meta.metrics[metric].key;
		if (range) this.zoom.set(key, range);
		else this.zoom.delete(key);
	}

	/** This project's panel layout. */
	get layout(): PanelLayout {
		return this.layouts[layoutKey(this.meta.entity, this.meta.project)] ?? EMPTY_LAYOUT;
	}

	/** Sections in the viewer's order, with their panels in the viewer's order. */
	sections = $derived.by((): SectionDef[] => {
		const { order, sections } = this.layout;
		const metrics = this.meta.metrics;
		return arrange(this.meta.sections, (s) => s.key, sections).map((sec) =>
			order[sec.key]
				? { ...sec, metrics: arrange(sec.metrics, (m) => metrics[m].key, order[sec.key]) }
				: sec
		);
	});

	/**
	 * Read this project's layout and hidden columns from the browser. Called after hydration,
	 * so the page the server rendered (in the default layout) is what hydrates.
	 */
	restoreLayout() {
		const { entity, project } = this.meta;
		const key = layoutKey(entity, project);
		if (!(key in this.layouts))
			this.layouts = { ...this.layouts, [key]: parseLayout(readStored(key)) };
		const cols = columnsKey(entity, project);
		if (!(cols in this.hiddenByProject))
			this.hiddenByProject = { ...this.hiddenByProject, [cols]: parseHidden(readStored(cols)) };
	}

	#saveLayout(layout: PanelLayout, persist = true) {
		const key = layoutKey(this.meta.entity, this.meta.project);
		this.layouts = { ...this.layouts, [key]: layout };
		if (persist) store(key, layout);
	}

	/** Move a panel to `to`, an index into its section's (arranged) panels. */
	movePanel(section: string, metric: number, to: number) {
		const sec = this.sections.find((s) => s.key === section);
		if (!sec) return;
		const keys = sec.metrics.map((m) => this.meta.metrics[m].key);
		const from = sec.metrics.indexOf(metric);
		const moved = moveKey(keys, from, to);
		if (from === -1 || moved[from] === keys[from]) return;
		const { order } = this.layout;
		this.#saveLayout({
			...this.layout,
			order: { ...order, [section]: mergeOrder(order[section] ?? [], moved) }
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

	// ------------------------------------------------------------------------------------------
	// Table columns the viewer hid, kept per project in this browser.

	/** Keys of this project's hidden columns. */
	hiddenColumns = $derived.by((): ReadonlySet<string> => {
		const key = columnsKey(this.meta.entity, this.meta.project);
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- replaced, never mutated
		return new Set(this.hiddenByProject[key] ?? NO_HIDDEN);
	});

	/** Indices into `meta.columns` of the columns the table shows, in order. */
	shownColumns = $derived.by(() => {
		const hidden = this.hiddenColumns;
		const out: number[] = [];
		this.meta.columns.forEach((c, i) => {
			if (!hidden.has(c.key) || LOCKED_COLUMNS.has(c.key)) out.push(i);
		});
		return out;
	});

	/** Show or hide some columns (the locked ones always show). */
	setColumnsHidden(keys: readonly string[], hide: boolean) {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a draft, saved as a list
		const next = new Set(this.hiddenColumns);
		for (const k of keys) {
			if (hide && !LOCKED_COLUMNS.has(k)) next.add(k);
			else next.delete(k);
		}
		this.#saveHidden([...next]);
	}

	/** Show every column again. */
	showAllColumns() {
		this.#saveHidden([]);
	}

	#saveHidden(keys: readonly string[]) {
		const key = columnsKey(this.meta.entity, this.meta.project);
		this.hiddenByProject = { ...this.hiddenByProject, [key]: keys };
		store(key, keys);
	}

	toggleSection(key: string) {
		toggle(this.collapsed, key);
	}

	setSectionPage(key: string, page: number) {
		this.sectionPage = { ...this.sectionPage, [key]: page };
	}
}

export const [getWorkspace, setWorkspace] = createContext<Workspace>();
