/**
 * How a viewer arranged and set up a project's charts, as kept in this browser. Pure helpers,
 * so the ordering rules can be tested without a workspace.
 */

/**
 * How the viewer set up one panel (W&B's "Edit panel"). Everything is optional: what is unset
 * follows the panel's defaults and the workspace's line plot settings.
 */
export interface PanelConfig {
	title?: string;
	/** Metric keys plotted, in place of the panel's own; all of the panel's stream. */
	metrics?: readonly string[];
	/**
	 * Its x axis (see `X_AXES` in #lib/chart/axes.ts, or a metric key), in place of the
	 * workspace's.
	 */
	xAxis?: string;
	/** Fixed x bounds, in the units of `xRangeAxis`; an unset end follows the data. */
	xMin?: number;
	xMax?: number;
	/**
	 * The axis (`XAxis.id`) the x bounds were set on; they apply only while the panel plots on
	 * it. Unset: `_step` (`_runtime` for system metrics), from before x axes could change.
	 */
	xRangeAxis?: string;
	xLabel?: string;
	/** Fixed y bounds in data units; an unset end follows the data. */
	yMin?: number;
	yMax?: number;
	yLabel?: string;
	ignoreOutliers?: boolean;
	/** Smoothing weight, 0 (none) to 0.99. */
	smoothing?: number;
	showOriginal?: boolean;
	/** In CSS pixels. */
	lineWidth?: number;
	/** Unset shows the legend when it names few enough lines. */
	legend?: boolean;
	log?: boolean;
}

/**
 * How the viewer arranged a project's panels: section order, per section its panel order, plot
 * height and rows per page, and per panel its settings.
 */
export interface PanelLayout {
	/** Section keys in the order they were arranged; sections logged since go after them. */
	sections: readonly string[];
	/** Panel ids in the order they were arranged; panels logged since go after them. */
	order: Readonly<Record<string, readonly string[]>>;
	height: Readonly<Record<string, number>>;
	/** Rows of panels a section's page shows, when the viewer resized it. */
	rows: Readonly<Record<string, number>>;
	/** By panel id. */
	panels: Readonly<Record<string, PanelConfig>>;
}

export const EMPTY_LAYOUT: PanelLayout = {
	sections: [],
	order: {},
	height: {},
	rows: {},
	panels: {}
};

export const SMOOTHING_MAX = 0.99;
export const LINE_WIDTH_MIN = 0.5;
export const LINE_WIDTH_MAX = 4;
export const ROWS_MIN = 1;
export const ROWS_MAX = 12;

/** Rows per page, a whole number of them within bounds. */
export const clampRows = (n: number) => Math.max(ROWS_MIN, Math.min(ROWS_MAX, Math.round(n)));

const isRecord = (v: unknown): v is Record<string, unknown> =>
	!!v && typeof v === 'object' && !Array.isArray(v);

const strings = (v: unknown): string[] =>
	Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : [];

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * One panel's settings, keeping only what is well formed: unknown fields and values of the
 * wrong type are dropped, numbers are clamped to what the editor offers. Null when nothing is
 * left, so a panel without settings has no entry.
 */
export function parsePanelConfig(raw: unknown): PanelConfig | null {
	if (!isRecord(raw)) return null;
	const out: Record<string, unknown> = {};
	for (const k of ['title', 'xLabel', 'yLabel', 'xAxis', 'xRangeAxis'] as const) {
		const v = raw[k];
		if (typeof v === 'string' && v.trim()) out[k] = v.trim();
	}
	const metrics = [...new Set(strings(raw.metrics))];
	if (metrics.length) out.metrics = metrics;
	for (const k of ['xMin', 'xMax', 'yMin', 'yMax'] as const) if (finite(raw[k])) out[k] = raw[k];
	// Bounds the wrong way round would draw nothing; they're dropped together.
	for (const [lo, hi] of [
		['xMin', 'xMax'],
		['yMin', 'yMax']
	] as const) {
		if (finite(out[lo]) && finite(out[hi]) && !(out[hi] > out[lo])) {
			delete out[lo];
			delete out[hi];
		}
	}
	for (const k of ['ignoreOutliers', 'showOriginal', 'legend', 'log'] as const)
		if (typeof raw[k] === 'boolean') out[k] = raw[k];
	if (finite(raw.smoothing)) out.smoothing = Math.max(0, Math.min(SMOOTHING_MAX, raw.smoothing));
	if (finite(raw.lineWidth))
		out.lineWidth = Math.max(LINE_WIDTH_MIN, Math.min(LINE_WIDTH_MAX, raw.lineWidth));
	if (out.xMin == null && out.xMax == null) delete out.xRangeAxis;
	return Object.keys(out).length ? (out as PanelConfig) : null;
}

/**
 * A layout read back from storage. Anything malformed is dropped rather than trusted; layouts
 * saved before sections could be moved (no `sections`) keep the default section order, those
 * saved before panels had settings (no `panels`) have none, and those saved before sections
 * could be resized to more rows (no `rows`) show the default number of rows.
 */
export function parseLayout(raw: unknown): PanelLayout {
	if (!isRecord(raw)) return EMPTY_LAYOUT;
	const order: Record<string, string[]> = {};
	if (isRecord(raw.order))
		for (const [k, v] of Object.entries(raw.order)) if (Array.isArray(v)) order[k] = strings(v);
	const height: Record<string, number> = {};
	if (isRecord(raw.height))
		for (const [k, v] of Object.entries(raw.height)) if (finite(v)) height[k] = v;
	const rows: Record<string, number> = {};
	if (isRecord(raw.rows))
		for (const [k, v] of Object.entries(raw.rows)) if (finite(v)) rows[k] = clampRows(v);
	const panels: Record<string, PanelConfig> = {};
	if (isRecord(raw.panels))
		for (const [k, v] of Object.entries(raw.panels)) {
			const config = parsePanelConfig(v);
			if (config) panels[k] = config;
		}
	return { sections: strings(raw.sections), order, height, rows, panels };
}

/**
 * A saved panel order in today's panel ids. Orders saved before system metrics were grouped
 * name metric keys (`system.gpu.0.gpu`); `alias` maps those to the panel now plotting them,
 * which takes the place of the first. Keys it doesn't know are kept as they are: logged
 * metrics are their own panel ids, and anything else is ignored by `arrange`.
 */
export function migrateOrder(
	saved: readonly string[],
	alias: (key: string) => string | undefined
): string[] {
	const out = new Set<string>();
	for (const k of saved) out.add(alias(k) ?? k);
	return [...out];
}

/**
 * `items` in a saved order: those named in `order` first, as ordered there; the rest after
 * them in their own (default) order.
 */
export function arrange<T>(
	items: readonly T[],
	keyOf: (item: T) => string,
	order: readonly string[] | undefined
): T[] {
	if (!order?.length) return [...items];
	const rank = new Map(order.map((k, i) => [k, i]));
	const at = (item: T) => rank.get(keyOf(item)) ?? Infinity;
	// A stable sort: unranked items (all Infinity) keep their order.
	return items.toSorted((a, b) => at(a) - at(b));
}

/**
 * The page, of `count` panels `per` to a page, that shows the panel at `start`: kept rather
 * than a page number, so the panels in view stay in view when pages hold more or fewer of
 * them. Within the pages there are.
 */
export function pageAt(start: number, per: number, count: number) {
	const pages = Math.max(1, Math.ceil(count / per));
	return Math.max(0, Math.min(Math.floor(start / per), pages - 1));
}

/** `keys` with the one at `from` moved to `to` (clamped to the list). */
export function moveKey(keys: readonly string[], from: number, to: number): string[] {
	const out = [...keys];
	if (from < 0 || from >= out.length) return out;
	to = Math.max(0, Math.min(out.length - 1, to));
	out.splice(to, 0, ...out.splice(from, 1));
	return out;
}

/**
 * The order to save once the keys shown here were rearranged to `arranged`. Keys saved before
 * that aren't shown here (a run's page shows only its own sections) keep their places; the
 * shown ones fill their slots in the new order.
 */
export function mergeOrder(saved: readonly string[], arranged: readonly string[]): string[] {
	const shown = new Set(arranged);
	const full = [...saved.filter((k, i) => saved.indexOf(k) === i)];
	for (const k of arranged) if (!full.includes(k)) full.push(k);
	let next = 0;
	return full.map((k) => (shown.has(k) ? arranged[next++] : k));
}
