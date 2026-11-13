/**
 * How a viewer arranged and set up a project's charts, as kept in this browser. Pure helpers,
 * so the ordering rules can be tested without a workspace.
 */

import {
	SMOOTHING_METHODS,
	clampSmoothing,
	isSmoothingType,
	type Smoothing,
	type SmoothingType
} from '#lib/chart/series.ts';

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
	/**
	 * Smoothing method. Unset with `smoothing` set is an EMA, as panels were smoothed before
	 * there were other methods.
	 */
	smoothingType?: SmoothingType;
	/** The method's parameter (see `SMOOTHING_METHODS`): an EMA weight, 0 (none) to 0.99. */
	smoothing?: number;
	showOriginal?: boolean;
	/** Extend each run's line with a fitted power law (see #lib/chart/forecast.ts). */
	forecast?: boolean;
	/** The x the forecast goes to; unset is twice the longest run's last step. */
	forecastTo?: number;
	/** In CSS pixels. */
	lineWidth?: number;
	/** Unset shows the legend when it names few enough lines. */
	legend?: boolean;
	log?: boolean;
}

/**
 * The workspace's line plot settings, for panels that don't set their own (`PanelConfig`), and
 * the rows a section shows unless it was resized. Unset follows the defaults.
 */
export interface PlotSettings {
	/** As in `PanelConfig`: unset with `smoothing` set is an EMA. */
	smoothingType?: SmoothingType;
	smoothing?: number;
	showOriginal?: boolean;
	ignoreOutliers?: boolean;
	forecast?: boolean;
	/** One of `X_AXES` in #lib/chart/axes.ts, or a metric key. */
	xAxis?: string;
	rows?: number;
}

/** Ids of panels the viewer added start with this, so none is ever a metric's panel id. */
export const CUSTOM_PREFIX = 'custom:';

/** The section of the panels the viewer added, first in the workspace by default. */
export const CUSTOM_SECTION = 'Custom panels';

export const isCustomPanel = (id: string) => id.startsWith(CUSTOM_PREFIX);

/** A line plot the viewer added: its settings, which always name the metrics it plots. */
export interface CustomPanel {
	id: string;
	config: PanelConfig & { metrics: readonly string[] };
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
	/** Ids of the panels pinned to the front of their sections. */
	pinned: readonly string[];
	settings: PlotSettings;
	/** Panels the viewer added, in the order they were added. */
	custom: readonly CustomPanel[];
}

export const EMPTY_LAYOUT: PanelLayout = {
	sections: [],
	order: {},
	height: {},
	rows: {},
	panels: {},
	pinned: [],
	settings: {},
	custom: []
};

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
 * Read a saved smoothing method and parameter into `out`: a known method with its parameter
 * clamped (or its starting one), else a weight alone as an EMA's.
 */
function readSmoothing(raw: Record<string, unknown>, out: Record<string, unknown>) {
	if (isSmoothingType(raw.smoothingType)) {
		out.smoothingType = raw.smoothingType;
		if (raw.smoothingType !== 'none')
			out.smoothing = clampSmoothing(
				raw.smoothingType,
				finite(raw.smoothing) ? raw.smoothing : SMOOTHING_METHODS[raw.smoothingType].start
			);
	} else if (finite(raw.smoothing)) out.smoothing = clampSmoothing('ema', raw.smoothing);
}

/** The workspace's line plot settings as saved, keeping only what is well formed. */
export function parsePlotSettings(raw: unknown): PlotSettings {
	if (!isRecord(raw)) return {};
	const out: Record<string, unknown> = {};
	readSmoothing(raw, out);
	for (const k of ['showOriginal', 'ignoreOutliers', 'forecast'] as const)
		if (typeof raw[k] === 'boolean') out[k] = raw[k];
	if (typeof raw.xAxis === 'string' && raw.xAxis.trim()) out.xAxis = raw.xAxis.trim();
	if (finite(raw.rows)) out.rows = clampRows(raw.rows);
	return out as PlotSettings;
}

/** Smoothing a panel (or the workspace) sets, or undefined when it sets none. */
export function panelSmoothing(config: {
	smoothingType?: SmoothingType;
	smoothing?: number;
}): Smoothing | undefined {
	const type = config.smoothingType ?? (config.smoothing != null ? 'ema' : undefined);
	if (!type) return undefined;
	return { type, param: config.smoothing ?? SMOOTHING_METHODS[type].start };
}

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
	for (const k of ['ignoreOutliers', 'showOriginal', 'legend', 'log', 'forecast'] as const)
		if (typeof raw[k] === 'boolean') out[k] = raw[k];
	if (finite(raw.forecastTo) && raw.forecastTo > 0) out.forecastTo = raw.forecastTo;
	readSmoothing(raw, out);
	if (finite(raw.lineWidth))
		out.lineWidth = Math.max(LINE_WIDTH_MIN, Math.min(LINE_WIDTH_MAX, raw.lineWidth));
	if (out.xMin == null && out.xMax == null) delete out.xRangeAxis;
	return Object.keys(out).length ? (out as PanelConfig) : null;
}

/**
 * Added panels as saved, keeping those with a well formed id (unique, with `CUSTOM_PREFIX`) and
 * settings that name at least one metric.
 */
export function parseCustomPanels(raw: unknown): CustomPanel[] {
	if (!Array.isArray(raw)) return [];
	const out: CustomPanel[] = [];
	for (const item of raw) {
		if (!isRecord(item)) continue;
		const { id } = item;
		if (typeof id !== 'string' || !isCustomPanel(id) || id.length <= CUSTOM_PREFIX.length) continue;
		if (out.some((c) => c.id === id)) continue;
		const config = parsePanelConfig(item.config);
		if (config?.metrics?.length) out.push({ id, config: { ...config, metrics: config.metrics } });
	}
	return out;
}

/**
 * A layout read back from storage. Anything malformed is dropped rather than trusted; layouts
 * saved before sections could be moved (no `sections`) keep the default section order, those
 * saved before panels had settings (no `panels`) have none, and those saved before sections
 * could be resized to more rows (no `rows`) show the default number of rows, and those saved
 * before panels could be pinned (no `pinned`) have none pinned. Those saved before the line plot
 * settings were kept (no `settings`) use the default ones, and those saved before panels could
 * be added (no `custom`) have none.
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
	return {
		sections: strings(raw.sections),
		order,
		height,
		rows,
		panels,
		pinned: [...new Set(strings(raw.pinned))],
		settings: parsePlotSettings(raw.settings),
		custom: parseCustomPanels(raw.custom)
	};
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
 * Index of the first of the `per` panels shown, of `count`, for a page left at `start`: kept
 * rather than a page number, so the panels in view stay in view when pages hold more or fewer
 * of them. Pages are always full: the last one ends at the last panel, sharing some with the
 * one before when they don't divide evenly, so a section keeps its height from page to page
 * and resizing it on any page shows more or fewer rows.
 */
export function pageStart(start: number, per: number, count: number) {
	return Math.max(0, Math.min(Math.floor(start), count - per));
}

/**
 * `keys` with the pinned ones first, each part in its own order. `loose` (a pinned key being
 * moved) keeps its place, wherever that is.
 */
export function pinFirst(
	keys: readonly string[],
	pinned: ReadonlySet<string>,
	loose: string | null = null
): string[] {
	const at = loose == null ? -1 : keys.indexOf(loose);
	const rest = at === -1 ? keys : keys.filter((k) => k !== loose);
	const out = [...rest.filter((k) => pinned.has(k)), ...rest.filter((k) => !pinned.has(k))];
	if (at !== -1) out.splice(at, 0, loose!);
	return out;
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
