/**
 * X axes other than the one a curve was sent against, as W&B offers them: a metric's step
 * metric (`wandb.define_metric(step_metric=...)`), wall-clock and process time, or any logged
 * metric. Curves always arrive against `_step` (system metrics: `_runtime`); each run also
 * carries mappings, curves of `_runtime`, `_timestamp` and its step metrics against that same x,
 * and a curve is put on another axis by interpolating them. So switching axes needs no request.
 */

import type { MetricDefinition, MetricStream } from '#lib/types.ts';
import {
	duration,
	formatAxisStep,
	formatNumber,
	formatRuntime,
	formatStep,
	formatTick,
	formatWallTick,
	formatWallTime
} from '#lib/utils.ts';
import { Series } from './series.ts';

/**
 * A run's x-axis mapping: one key's values (`y`) at ascending x (`_step`, or `_runtime` for
 * system metrics), growable for live runs.
 */
export class AxisCurve {
	n = 0;
	private xs: Float64Array;
	private ys: Float64Array;
	/** Bumped on every change, for curves remapped through it. */
	version = 0;

	/** Live mappings are halved past this; mappings kept knots-only rarely get close. */
	static MAX_POINTS = 8192;

	constructor(x: ArrayLike<number> = [], y: ArrayLike<number> = []) {
		this.xs = Float64Array.from(x);
		this.ys = Float64Array.from(y);
		this.n = Math.min(this.xs.length, this.ys.length);
	}

	get x(): Float64Array {
		return this.xs.subarray(0, this.n);
	}

	get y(): Float64Array {
		return this.ys.subarray(0, this.n);
	}

	/**
	 * Append points after the last one; returns false if nothing was new. A point in line with
	 * the two before it replaces the last one, which interpolates the same: a step metric
	 * logged at a fixed stride stays two points long.
	 */
	append(x: ArrayLike<number>, y: ArrayLike<number | null>): boolean {
		let changed = false;
		for (let i = 0; i < x.length; i++) {
			const xi = x[i];
			const yi = y[i] ?? NaN;
			const n = this.n;
			if (n && !(xi > this.xs[n - 1])) continue;
			changed = true;
			if (n >= 2) {
				const [xa, ya, xb, yb] = [this.xs[n - 2], this.ys[n - 2], this.xs[n - 1], this.ys[n - 1]];
				if ((yb - ya) * (xi - xb) === (yi - yb) * (xb - xa)) {
					this.xs[n - 1] = xi;
					this.ys[n - 1] = yi;
					continue;
				}
			}
			if (n === this.xs.length) {
				const cap = Math.max(16, n * 2);
				const nx = new Float64Array(cap);
				const ny = new Float64Array(cap);
				nx.set(this.xs);
				ny.set(this.ys);
				this.xs = nx;
				this.ys = ny;
			}
			this.xs[n] = xi;
			this.ys[n] = yi;
			this.n = n + 1;
			if (this.n > AxisCurve.MAX_POINTS) this.halve();
		}
		if (changed) this.version++;
		return changed;
	}

	/** Keep every other point, and always the newest. */
	private halve() {
		let j = 0;
		for (let i = 0; i < this.n; i += 2, j++) {
			this.xs[j] = this.xs[i];
			this.ys[j] = this.ys[i];
		}
		if ((this.n - 1) % 2) {
			this.xs[j] = this.xs[this.n - 1];
			this.ys[j] = this.ys[this.n - 1];
			j++;
		}
		this.n = j;
	}

	/** First value, or NaN without any. */
	get first(): number {
		return this.n ? this.ys[0] : NaN;
	}
}

/** Mappings of one run, by key, for each stream's x. */
export type RunAxes = Record<MetricStream, Map<string, AxisCurve>>;

export const noAxes = (): RunAxes => ({ history: new Map(), system: new Map() });

/** Something to map x values through: a mapping, or a logged metric's own curve. */
export interface MapSource {
	readonly n: number;
	readonly version: number;
	readonly x: ArrayLike<number>;
	readonly y: ArrayLike<number>;
}

/**
 * `series` with its x values replaced by `map`'s values there (plus `offset`), interpolated
 * linearly between the map's points. Points outside the map's x range, or where it isn't
 * finite, are dropped. The result is in x order, as W&B plots it; points keep their order
 * where x ties, so a metric that goes back and forth stays drawn as logged.
 */
export function remap(series: Series, map: MapSource, offset = 0): Series {
	const n = series.n;
	const mx = map.x;
	const my = map.y;
	const mn = map.n;
	const sy = series.y;
	const xs = new Float64Array(n);
	const ys = new Float32Array(n);
	let k = 0;
	let j = 0;
	let sorted = true;
	for (let i = 0; i < n && mn; i++) {
		const s = series.stepAt(i);
		if (s < mx[0] || s > mx[mn - 1]) continue;
		// Steps ascend, so the map is walked once.
		while (j < mn - 2 && mx[j + 1] <= s) j++;
		let v: number;
		if (mn === 1 || s <= mx[j]) v = my[j];
		else if (s >= mx[j + 1]) v = my[j + 1];
		else v = my[j] + ((my[j + 1] - my[j]) * (s - mx[j])) / (mx[j + 1] - mx[j]);
		if (!Number.isFinite(v)) continue;
		v += offset;
		if (k && v < xs[k - 1]) sorted = false;
		xs[k] = v;
		ys[k] = sy[i];
		k++;
	}
	if (sorted) return new Series(ys.slice(0, k), xs.slice(0, k));
	const order = Array.from({ length: k }, (_, i) => i).sort((a, b) => xs[a] - xs[b] || a - b);
	return new Series(
		Float32Array.from(order, (i) => ys[i]),
		Float64Array.from(order, (i) => xs[i])
	);
}

interface Remapped {
	map: MapSource;
	version: number;
	mapVersion: number;
	offset: number;
	out: Series;
}

const cache = new WeakMap<Series, Map<string, Remapped>>();

/** `remap`, memoised per series and axis until either curve (or the offset) changes. */
export function remapped(series: Series, axis: string, map: MapSource, offset = 0): Series {
	let byAxis = cache.get(series);
	if (!byAxis) cache.set(series, (byAxis = new Map()));
	const hit = byAxis.get(axis);
	if (
		hit &&
		hit.map === map &&
		hit.version === series.version &&
		hit.mapVersion === map.version &&
		hit.offset === offset
	)
		return hit.out;
	const out = remap(series, map, offset);
	byAxis.set(axis, { map, version: series.version, mapVersion: map.version, offset, out });
	return out;
}

// ------------------------------------------------------------------------------------------
// Metric definitions

/**
 * The definition that applies to `key`: its own, else the glob with the longest prefix it
 * starts with (wandb-core's rule: globs only end in `*`), else none.
 */
export function definitionOf(
	defs: readonly MetricDefinition[],
	key: string
): MetricDefinition | null {
	let glob: MetricDefinition | null = null;
	for (const d of defs) {
		if (d.key === key) return d;
		if (d.glob && key.startsWith(d.glob.slice(0, -1)))
			if (!glob || d.glob.length > glob.glob!.length) glob = d;
	}
	return glob;
}

/** The step metric `key` is plotted against by default, if its run defined one. */
export function stepMetricOf(defs: readonly MetricDefinition[], key: string): string | null {
	const step = definitionOf(defs, key)?.step;
	return step && step !== key ? step : null;
}

/** What `sharedStepMetric` knows of the runs' data, by run index. */
export interface StepMetricData {
	/** Whether the run logged the metric: it has a line to plot. */
	logged(run: number): boolean;
	/** Whether the run's x values on `step` are known: it logged that step metric. */
	maps(run: number, step: string): boolean;
}

/**
 * The step metric of `key` for a chart of several runs, which has one x axis: the first run
 * that defined `key` (by name or glob) decides, so an explicit "no step metric" counts too.
 * Runs that defined nothing don't.
 *
 * With `data`, only runs that logged `key` decide: a glob such as Hugging Face's `*` of a run
 * that never logged the metric says nothing about it. And a step metric some run that logged
 * `key` didn't log is no axis for that run's line, which would be left off the chart: the chart
 * stays on `_step` then (null), where every line is.
 */
export function sharedStepMetric(
	runs: readonly (readonly MetricDefinition[])[],
	key: string,
	data?: StepMetricData
): string | null {
	let of = runs.map((_, i) => i);
	if (data) {
		const logged = of.filter((i) => data.logged(i));
		// None did (yet): no lines to plot either way, so the definitions alone decide.
		if (logged.length) of = logged;
	}
	const first = of.find((i) => definitionOf(runs[i], key));
	const step = first === undefined ? null : stepMetricOf(runs[first], key);
	if (!step || !data) return step;
	return of.every((i) => !data.logged(i) || data.maps(i, step)) ? step : null;
}

// ------------------------------------------------------------------------------------------
// Axis choices

/** How an axis' values read: steps, other numbers, seconds of duration, or epoch seconds. */
export type AxisKind = 'step' | 'number' | 'duration' | 'time';

/**
 * W&B's x-axis choices besides metrics; a choice is one of these values, or a metric key.
 * Keys starting with `_` are wandb's, never a metric's.
 */
export const X_AXES = [
	{ value: '_step', label: 'Step' },
	{ value: '_relative_time', label: 'Relative Time (Wall)' },
	{ value: '_runtime', label: 'Relative Time (Process)' },
	{ value: '_timestamp', label: 'Wall Time' }
] as const;

/** The default: each metric's step metric, else `_step`. */
export const DEFAULT_X_AXIS = '_step';

/** What a choice is called in pickers. */
export const axisChoiceLabel = (choice: string) =>
	X_AXES.find((a) => a.value === choice)?.label ?? choice;

/** Choices system metrics can use: the times. Anything else leaves them on process time. */
export const SYSTEM_X_AXES: readonly string[] = ['_relative_time', '_runtime', '_timestamp'];

/** A choice made concrete for one panel. */
export interface XAxis {
	/**
	 * What the x values are (`_step`, `_runtime`, `train/global_step`...): panels on the same
	 * one share the crosshair, and zoom and set ranges are kept in its units.
	 */
	id: string;
	kind: AxisKind;
	/** The axis title. */
	label: string;
	/** The mapping that gives a run's x values, or null for the stream's own x. */
	map: string | null;
	/** Wall time since the run started, rather than the time of day. */
	relative: boolean;
}

const TIME: Record<string, Omit<XAxis, 'map'>> = {
	_relative_time: {
		id: '_relative_time',
		kind: 'duration',
		label: 'Relative Time (Wall)',
		relative: true
	},
	_runtime: { id: '_runtime', kind: 'duration', label: 'Relative Time (Process)', relative: false },
	_timestamp: { id: '_timestamp', kind: 'time', label: 'Wall Time', relative: false }
};

/**
 * The axis a panel of `stream` plots on for `choice`. `step` is the step metric of the
 * panel's metric, which "Step" means when there is one.
 */
export function resolveAxis(choice: string, stream: MetricStream, step: string | null): XAxis {
	const time = TIME[choice];
	if (stream === 'system') {
		if (choice === '_runtime' || !time) return { ...TIME._runtime, map: null };
		return { ...time, map: '_timestamp' };
	}
	if (time) return { ...time, map: choice === '_runtime' ? '_runtime' : '_timestamp' };
	const key = choice === DEFAULT_X_AXIS ? step : choice;
	if (!key) return { id: '_step', kind: 'step', label: 'Step', map: null, relative: false };
	return { id: key, kind: 'number', label: key, map: key, relative: false };
}

/** An x tick's label; `step` is the tick spacing. */
export function formatXTick(axis: XAxis, t: number, step: number): string {
	switch (axis.kind) {
		case 'step':
			return formatAxisStep(t);
		case 'duration':
			return formatRuntime(t, step);
		case 'time':
			return formatWallTick(t, step);
		default:
			return formatTick(t, step, 'num');
	}
}

/** Where the crosshair is, for chart tooltips: `Step 1,200`, `Runtime 3m 05s`... */
export function formatX(axis: XAxis, v: number): string {
	switch (axis.kind) {
		case 'step':
			return `Step ${formatXValue(axis, v)}`;
		case 'duration':
			return `${axis.relative ? 'Wall time' : 'Runtime'} ${formatXValue(axis, v)}`;
		case 'time':
			return formatXValue(axis, v);
		default:
			return `${axis.label} ${formatXValue(axis, v)}`;
	}
}

/** An x value without the axis' name (`1,200`, `3m 05s`), for a tooltip row under `formatX`. */
export function formatXValue(axis: XAxis, v: number): string {
	switch (axis.kind) {
		case 'step':
			return formatStep(v);
		case 'duration':
			// Short runs read to the hundredth of a second.
			return Math.abs(v) < 10 ? `${+v.toFixed(2)}s` : duration(v);
		case 'time':
			return formatWallTime(v);
		default:
			return formatNumber(v);
	}
}
