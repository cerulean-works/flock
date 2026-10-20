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

/**
 * Debiased exponential moving average, the same smoothing W&B applies by default.
 * `weight` 0 returns the input unchanged.
 */
export function ema(src: Float32Array, weight: number): Float32Array {
	if (weight <= 0) return src;
	const out = new Float32Array(src.length);
	let acc = 0;
	let norm = 0;
	for (let i = 0; i < src.length; i++) {
		const v = src[i];
		if (Number.isNaN(v)) {
			out[i] = NaN;
			continue;
		}
		acc = acc * weight + (1 - weight) * v;
		norm = norm * weight + (1 - weight);
		out[i] = acc / norm;
	}
	return out;
}

const smoothCache = new WeakMap<
	Series,
	{ weight: number; version: number; values: Float32Array }
>();

/** Smoothed values of a series, memoised until the series or the weight changes. */
export function smoothed(series: Series, weight: number): Float32Array {
	if (weight <= 0) return series.y;
	const hit = smoothCache.get(series);
	if (hit && hit.weight === weight && hit.version === series.version) return hit.values;
	const values = ema(series.y, weight);
	smoothCache.set(series, { weight, version: series.version, values });
	return values;
}

/** Series slot of page row `row` and metric `metric`. */
export const slotOf = (metricCount: number, row: number, metric: number) =>
	row * metricCount + metric;
