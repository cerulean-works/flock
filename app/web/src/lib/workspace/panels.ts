/**
 * Panels as the viewer set them up: a panel's defaults (`PanelDef`) with its settings
 * (`PanelConfig`) applied, and the editor's form for those settings. Pure, so the rules can be
 * tested without a workspace.
 */

import type { MetricDef, MetricStream, PanelDef } from '#lib/types.ts';
import { LINE_WIDTH_MAX, LINE_WIDTH_MIN, SMOOTHING_MAX, type PanelConfig } from './layout.ts';

/** A panel as it is drawn. */
export interface PanelView extends PanelDef {
	/** Its settings; empty when it has none. */
	config: PanelConfig;
	/** Length of the title's muted prefix: `train/` of `train/loss`, none for a set title. */
	prefix: number;
}

const NO_CONFIG: PanelConfig = {};

/**
 * `def` with `config` applied. Metrics are looked up by key in the panel's stream; those this
 * page hasn't logged are skipped, and a panel left with none plots its own.
 */
export function resolvePanel(
	def: PanelDef,
	config: PanelConfig | undefined,
	metrics: readonly MetricDef[],
	find: (stream: MetricStream, key: string) => number | undefined
): PanelView {
	config ??= NO_CONFIG;
	let of = def.metrics;
	if (config.metrics) {
		const picked = config.metrics
			.map((k) => find(def.stream, k))
			.filter((m): m is number => m !== undefined);
		if (picked.length) of = picked;
	}
	const own = of === def.metrics;
	// A panel plotting other metrics than its own is named after them, as W&B does.
	const title = config.title ?? (own ? def.title : of.map((m) => metrics[m].label).join(', '));
	const prefix =
		!config.title && def.stream === 'history' && of.length === 1 ? title.indexOf('/') + 1 : 0;
	return {
		...def,
		title,
		format: metrics[of[0]]?.format ?? def.format,
		metrics: of,
		config,
		prefix
	};
}

// ------------------------------------------------------------------------------------------
// The editor's form. Numbers are kept as typed, so a half-typed `1e-` isn't lost.

export type Tristate = 'inherit' | 'on' | 'off';

export interface PanelDraft {
	title: string;
	metrics: string[];
	/** Null follows the workspace. */
	xAxis: string | null;
	xMin: string;
	xMax: string;
	xLabel: string;
	yMin: string;
	yMax: string;
	yLabel: string;
	ignoreOutliers: Tristate;
	/** Null follows the workspace. */
	smoothing: number | null;
	showOriginal: Tristate;
	/** Null for the default width. */
	lineWidth: number | null;
	legend: Tristate;
	log: boolean;
}

/** Where a draft can't be applied, by field. */
export type DraftErrors = Partial<Record<'xMin' | 'xMax' | 'yMin' | 'yMax' | 'metrics', string>>;

const tri = (v: boolean | undefined): Tristate => (v == null ? 'inherit' : v ? 'on' : 'off');
const untri = (v: Tristate) => (v === 'inherit' ? undefined : v === 'on');
const num = (v: number | undefined) => (v == null ? '' : String(v));

/**
 * The form for a panel's settings; `own` is its default metric keys. `rangeAxis` is the axis
 * (`XAxis.id`) it plots on: x bounds set on another one are left out, as they don't apply.
 */
export function draftOf(
	config: PanelConfig,
	own: readonly string[],
	rangeAxis?: string
): PanelDraft {
	const keep = rangeAxis == null || config.xRangeAxis == null || config.xRangeAxis === rangeAxis;
	return {
		title: config.title ?? '',
		metrics: [...(config.metrics ?? own)],
		xAxis: config.xAxis ?? null,
		xMin: keep ? num(config.xMin) : '',
		xMax: keep ? num(config.xMax) : '',
		xLabel: config.xLabel ?? '',
		yMin: num(config.yMin),
		yMax: num(config.yMax),
		yLabel: config.yLabel ?? '',
		ignoreOutliers: tri(config.ignoreOutliers),
		smoothing: config.smoothing ?? null,
		showOriginal: tri(config.showOriginal),
		lineWidth: config.lineWidth ?? null,
		legend: tri(config.legend),
		log: config.log ?? false
	};
}

/** A typed bound: blank is unset, anything else must be a finite number. */
function bound(text: string): number | undefined | 'invalid' {
	const t = text.trim();
	if (!t) return undefined;
	const v = Number(t);
	return Number.isFinite(v) ? v : 'invalid';
}

/**
 * The settings a draft stands for, and what is wrong with it. Whatever equals the panel's
 * defaults is left unset (a title of its own name, its own metrics in order), so a panel set
 * back by hand has no settings, and later changes to its defaults reach it. x bounds are kept
 * with the axis (`XAxis.id`) they're on, `rangeAxis`.
 */
export function configOf(
	draft: PanelDraft,
	defaults: { title: string; metrics: readonly string[] },
	rangeAxis?: string
): { config: PanelConfig; errors: DraftErrors } {
	const config: PanelConfig = {};
	const errors: DraftErrors = {};
	const title = draft.title.trim();
	if (title && title !== defaults.title) config.title = title;
	if (!draft.metrics.length) errors.metrics = 'Plot at least one metric';
	else if (
		draft.metrics.length !== defaults.metrics.length ||
		draft.metrics.some((k, i) => k !== defaults.metrics[i])
	)
		config.metrics = [...draft.metrics];
	if (draft.xAxis != null) config.xAxis = draft.xAxis;
	for (const [lo, hi] of [
		['xMin', 'xMax'],
		['yMin', 'yMax']
	] as const) {
		const a = bound(draft[lo]);
		const b = bound(draft[hi]);
		if (a === 'invalid') errors[lo] = 'Not a number';
		else if (a !== undefined) config[lo] = a;
		if (b === 'invalid') errors[hi] = 'Not a number';
		else if (b !== undefined) config[hi] = b;
		// Left out of the preview too, which would otherwise show an empty sliver.
		if (typeof a === 'number' && typeof b === 'number' && !(b > a)) {
			errors[hi] = 'Must be above the minimum';
			delete config[lo];
			delete config[hi];
		}
	}
	if (rangeAxis != null && (config.xMin != null || config.xMax != null))
		config.xRangeAxis = rangeAxis;
	for (const k of ['xLabel', 'yLabel'] as const) {
		const v = draft[k].trim();
		if (v) config[k] = v;
	}
	const ignoreOutliers = untri(draft.ignoreOutliers);
	if (ignoreOutliers != null) config.ignoreOutliers = ignoreOutliers;
	if (draft.smoothing != null)
		config.smoothing = Math.max(0, Math.min(SMOOTHING_MAX, draft.smoothing));
	const showOriginal = untri(draft.showOriginal);
	if (showOriginal != null) config.showOriginal = showOriginal;
	if (draft.lineWidth != null)
		config.lineWidth = Math.max(LINE_WIDTH_MIN, Math.min(LINE_WIDTH_MAX, draft.lineWidth));
	const legend = untri(draft.legend);
	if (legend != null) config.legend = legend;
	if (draft.log) config.log = true;
	return { config, errors };
}
