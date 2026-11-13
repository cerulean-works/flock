/**
 * One run's curve for one metric: steps (`x`) and values (`y`), growable for live runs.
 *
 * Snapshots usually send evenly stepped series without x values: point `i` sits at raw index
 * `k_i = floor((i·(N-1) + floor((n-1)/2)) / (n-1))`, i.e. step `x0 + dx·k_i`, matching the
 * flock's sampling exactly. Those x values are only materialized when something needs the
 * whole array (drawing, appending); point lookups are computed.
 */
export class Series {
	n: number;
	/** Values; capacity may exceed `n`. */
	private ys: Float32Array;
	private xs: Float64Array | null;
	private readonly x0: number;
	private readonly dx: number;
	private readonly raw: number;
	/** Extent of the finite values: an overflowed (±inf) or missing (NaN) point is a gap. */
	min = Infinity;
	max = -Infinity;
	/** Bumped on every change; caches derived from the series (smoothing) check it. */
	version = 0;

	/** Live series are halved when they grow past this, so memory and draw cost stay bounded. */
	static MAX_POINTS = 4096;

	constructor(
		y: Float32Array,
		x: Float64Array | null,
		regular: { x0: number; dx: number; raw: number } = { x0: 0, dx: 1, raw: y.length }
	) {
		this.n = y.length;
		this.ys = y;
		this.xs = x;
		this.x0 = regular.x0;
		this.dx = regular.dx;
		this.raw = regular.raw;
		this.extent(0);
	}

	/** Values, as a view of the first `n`. */
	get y(): Float32Array {
		return this.ys.subarray(0, this.n);
	}

	/** Steps, as a view of the first `n`. */
	get x(): Float64Array {
		if (!this.xs) {
			const xs = new Float64Array(this.ys.length);
			for (let i = 0; i < this.n; i++) xs[i] = this.regularStep(i);
			this.xs = xs;
		}
		return this.xs.subarray(0, this.n);
	}

	private regularStep(i: number): number {
		const n = this.n;
		if (n <= 1) return this.x0;
		return this.x0 + this.dx * Math.floor((i * (this.raw - 1) + Math.floor((n - 1) / 2)) / (n - 1));
	}

	stepAt(i: number): number {
		return this.xs ? this.xs[i] : this.regularStep(i);
	}

	get firstStep(): number {
		return this.n ? this.stepAt(0) : 0;
	}

	get lastStep(): number {
		return this.n ? this.stepAt(this.n - 1) : 0;
	}

	/** Index of the point nearest to `step`, clamped to the series. */
	indexNear(step: number): number {
		const n = this.n;
		if (n <= 1) return 0;
		let lo: number;
		if (!this.xs) {
			const k = (step - this.x0) / this.dx;
			lo = Math.round((k * (n - 1)) / (this.raw - 1));
			lo = Math.max(0, Math.min(n - 1, lo));
			// The estimate is within one sample; settle on the true nearest.
			while (lo > 0 && Math.abs(this.stepAt(lo - 1) - step) < Math.abs(this.stepAt(lo) - step))
				lo--;
			while (lo < n - 1 && Math.abs(this.stepAt(lo + 1) - step) < Math.abs(this.stepAt(lo) - step))
				lo++;
			return lo;
		}
		const xs = this.xs;
		lo = 0;
		let hi = n - 1;
		while (lo < hi) {
			const mid = (lo + hi) >> 1;
			if (xs[mid] < step) lo = mid + 1;
			else hi = mid;
		}
		return lo > 0 && step - xs[lo - 1] < xs[lo] - step ? lo - 1 : lo;
	}

	/** Append points (steps ascending, after the last one); returns false if nothing was new. */
	append(steps: ArrayLike<number>, values: ArrayLike<number | null>): boolean {
		const last = this.n ? this.lastStep : -Infinity;
		let start = 0;
		while (start < steps.length && steps[start] <= last) start++;
		const count = steps.length - start;
		if (count <= 0) return false;
		const xs = this.x; // materialize before growing
		const need = this.n + count;
		let buf = this.xs!;
		if (need > this.ys.length || buf.length < need) {
			const cap = Math.max(16, need, this.ys.length * 2);
			const ny = new Float32Array(cap);
			ny.set(this.y);
			buf = new Float64Array(cap);
			buf.set(xs);
			this.ys = ny;
			this.xs = buf;
		}
		const from = this.n;
		for (let i = 0; i < count; i++) {
			buf[from + i] = steps[start + i];
			this.ys[from + i] = values[start + i] ?? NaN;
		}
		this.n = need;
		this.extent(from);
		if (this.n > Series.MAX_POINTS) this.halve();
		this.version++;
		return true;
	}

	/** Keep every other point (and always the newest), refreshing the extent. */
	private halve() {
		const xs = this.xs!;
		const ys = this.ys;
		let j = 0;
		for (let i = 0; i < this.n; i += 2, j++) {
			xs[j] = xs[i];
			ys[j] = ys[i];
		}
		if ((this.n - 1) % 2) {
			xs[j] = xs[this.n - 1];
			ys[j] = ys[this.n - 1];
			j++;
		}
		this.n = j;
		this.min = Infinity;
		this.max = -Infinity;
		this.extent(0);
	}

	private extent(from: number) {
		const ys = this.ys;
		for (let i = from; i < this.n; i++) {
			const v = ys[i];
			// One inf (a perplexity past float32's range) would leave no finite domain to draw in.
			if (!Number.isFinite(v)) continue;
			if (v < this.min) this.min = v;
			if (v > this.max) this.max = v;
		}
	}
}

/** Pixels from the cursor within which a point past a line's ends (or a lone one) reads out. */
export const SNAP_PX = 8;

/**
 * Index of the point of `s` that reads out at `step`, or -1 for none: its nearest point, while
 * `step` is within the line's span or within `tol` (in steps) of that point. A line logged once,
 * or past its ends, reads out near its points rather than only at their exact steps.
 */
export function pointAt(s: Series, step: number, tol: number): number {
	if (!s.n) return -1;
	const i = s.indexNear(step);
	if (Math.abs(s.stepAt(i) - step) <= tol) return i;
	return step >= s.firstStep && step <= s.lastStep ? i : -1;
}

/**
 * The logged step nearest to `step` among the points of `lines`, for a cursor to snap to: a
 * readout is then of points actually logged there. `step` itself when they have none.
 */
export function snapStep(lines: Iterable<Series>, step: number): number {
	let best = step;
	let dist = Infinity;
	for (const s of lines) {
		if (!s.n) continue;
		const x = s.stepAt(s.indexNear(step));
		const d = Math.abs(x - step);
		if (d < dist) {
			dist = d;
			best = x;
		}
	}
	return best;
}

/** How a line is smoothed: W&B's five methods. */
export type SmoothingType = 'none' | 'ema' | 'twema' | 'average' | 'gaussian';

/** A smoothing method and its parameter (a weight, or a window or sigma in points). */
export interface Smoothing {
	type: SmoothingType;
	param: number;
}

/** Each method's name, the bounds and step of its parameter, and the value it starts at. */
export const SMOOTHING_METHODS: Record<
	SmoothingType,
	{ label: string; param: string; min: number; max: number; step: number; start: number }
> = {
	none: { label: 'No smoothing', param: '', min: 0, max: 0, step: 1, start: 0 },
	ema: {
		label: 'Exponential moving average',
		param: 'Weight',
		min: 0,
		max: 0.99,
		step: 0.01,
		start: 0.6
	},
	twema: {
		label: 'Time weighted EMA',
		param: 'Weight',
		min: 0,
		max: 0.99,
		step: 0.01,
		start: 0.6
	},
	average: { label: 'Running average', param: 'Window', min: 1, max: 100, step: 1, start: 10 },
	gaussian: { label: 'Gaussian', param: 'Sigma', min: 0.1, max: 100, step: 0.1, start: 10 }
};

export const SMOOTHING_TYPES = Object.keys(SMOOTHING_METHODS) as SmoothingType[];

export const NO_SMOOTHING: Smoothing = { type: 'none', param: 0 };

export const isSmoothingType = (v: unknown): v is SmoothingType =>
	typeof v === 'string' && v in SMOOTHING_METHODS;

/** `param` within what `type` takes (a whole number of points for a running average). */
export function clampSmoothing(type: SmoothingType, param: number): number {
	const { min, max } = SMOOTHING_METHODS[type];
	if (type === 'none') return 0;
	const v = Math.max(min, Math.min(max, Number.isFinite(param) ? param : min));
	return type === 'average' ? Math.round(v) : v;
}

/** `s`, or no smoothing when its parameter leaves the line as it is (an EMA weight of 0). */
export const normalSmoothing = (s: Smoothing): Smoothing => (smooths(s) ? s : NO_SMOOTHING);

/** Whether `s` changes a line at all: a zero weight or a window of one leaves it as it is. */
export function smooths(s: Smoothing): boolean {
	switch (s.type) {
		case 'none':
			return false;
		case 'ema':
		case 'twema':
			return s.param > 0;
		case 'average':
			return s.param > 1;
		case 'gaussian':
			return s.param > 0;
	}
}

/**
 * Debiased exponential moving average, the same smoothing W&B applies by default.
 * `weight` 0 returns the input unchanged. Non-finite points stay gaps and don't feed the average,
 * so one inf doesn't turn the rest of the smoothed line into inf.
 */
export function ema(src: Float32Array, weight: number): Float32Array {
	if (weight <= 0) return src;
	const out = new Float32Array(src.length);
	let acc = 0;
	let norm = 0;
	for (let i = 0; i < src.length; i++) {
		const v = src[i];
		if (!Number.isFinite(v)) {
			out[i] = NaN;
			continue;
		}
		acc = acc * weight + (1 - weight) * v;
		norm = norm * weight + (1 - weight);
		out[i] = acc / norm;
	}
	return out;
}

/**
 * Time weighted EMA: a debiased EMA whose decay between two points scales with the gap between
 * their steps. `weight` is the decay over the average gap, so an evenly logged line smooths
 * exactly as `ema` smooths it, and an unevenly logged one smooths the same.
 */
export function timeWeightedEma(xs: Float64Array, src: Float32Array, weight: number): Float32Array {
	if (weight <= 0) return src;
	const n = Math.min(xs.length, src.length);
	let first = NaN;
	let last = NaN;
	let count = 0;
	for (let i = 0; i < n; i++) {
		if (!Number.isFinite(src[i])) continue;
		if (Number.isNaN(first)) first = xs[i];
		last = xs[i];
		count++;
	}
	const gap = count > 1 ? (last - first) / (count - 1) : 1;
	const out = new Float32Array(src.length).fill(NaN);
	let acc = 0;
	let norm = 0;
	let prev = NaN;
	for (let i = 0; i < n; i++) {
		const v = src[i];
		if (!Number.isFinite(v)) continue;
		const w = Number.isNaN(prev) || !(gap > 0) ? weight : weight ** ((xs[i] - prev) / gap);
		acc = acc * w + (1 - w) * v;
		norm = norm * w + (1 - w);
		out[i] = acc / norm;
		prev = xs[i];
	}
	return out;
}

/** Mean of the last `window` finite points, this one included. Non-finite points stay gaps. */
export function runningAverage(src: Float32Array, window: number): Float32Array {
	const size = Math.max(1, Math.round(window));
	if (size <= 1) return src;
	const out = new Float32Array(src.length).fill(NaN);
	const kept = new Float64Array(size);
	let sum = 0;
	let count = 0;
	for (let i = 0; i < src.length; i++) {
		const v = src[i];
		if (!Number.isFinite(v)) continue;
		if (count >= size) sum -= kept[count % size];
		kept[count % size] = v;
		sum += v;
		count++;
		out[i] = sum / Math.min(count, size);
	}
	return out;
}

/**
 * A centered Gaussian kernel over the points (`sigma` in points, like a running average's
 * window), truncated at 3 sigma: each point is the weighted mean of the finite points within
 * that many points of it. Non-finite points stay gaps.
 */
export function gaussian(src: Float32Array, sigma: number): Float32Array {
	if (!(sigma > 0)) return src;
	const n = src.length;
	const out = new Float32Array(n).fill(NaN);
	const reach = Math.floor(3 * sigma);
	const k = -1 / (2 * sigma * sigma);
	for (let i = 0; i < n; i++) {
		if (!Number.isFinite(src[i])) continue;
		let sum = 0;
		let norm = 0;
		for (let j = Math.max(0, i - reach); j <= Math.min(n - 1, i + reach); j++) {
			const v = src[j];
			if (!Number.isFinite(v)) continue;
			const d = j - i;
			const w = Math.exp(d * d * k);
			sum += w * v;
			norm += w;
		}
		out[i] = sum / norm;
	}
	return out;
}

/** `src` (with steps `xs`) smoothed by `s`; `src` itself when `s` leaves it as it is. */
export function smooth(xs: Float64Array, src: Float32Array, s: Smoothing): Float32Array {
	if (!smooths(s)) return src;
	switch (s.type) {
		case 'ema':
			return ema(src, s.param);
		case 'twema':
			return timeWeightedEma(xs, src, s.param);
		case 'average':
			return runningAverage(src, s.param);
		case 'gaussian':
			return gaussian(src, s.param);
		default:
			return src;
	}
}

const smoothCache = new WeakMap<
	Series,
	{ type: SmoothingType; param: number; version: number; values: Float32Array }
>();

/** Smoothed values of a series, memoised until the series, the method or its parameter change. */
export function smoothed(series: Series, s: Smoothing): Float32Array {
	if (!smooths(s)) return series.y;
	const hit = smoothCache.get(series);
	if (hit && hit.type === s.type && hit.param === s.param && hit.version === series.version)
		return hit.values;
	const values = smooth(series.x, series.y, s);
	smoothCache.set(series, { type: s.type, param: s.param, version: series.version, values });
	return values;
}

/** Series slot of page row `row` and metric `metric`. */
export const slotOf = (metricCount: number, row: number, metric: number) =>
	row * metricCount + metric;
