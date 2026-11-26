/**
 * Seed bands (D222): one mean line with a shaded band, across the runs of a configuration
 * that differ only in their seed.
 *
 * Alignment. Runs are sampled at their own x values (the history API downsamples each curve
 * to evenly spaced rows, and runs of different lengths land on different steps), so they are
 * first put on one x grid:
 *
 * - when every run has exactly the same x values (seeds of one config, logged alike), the grid
 *   is those values, and nothing is interpolated;
 * - otherwise it is evenly spaced over the union of the runs' spans, with as many points as the
 *   longest run (at most `GRID_MAX`), and each run is interpolated linearly onto it.
 *
 * A run is never extrapolated: it has no value at grid points before its first point or after
 * its last, nor across a gap (a point that isn't finite). So runs of different lengths are
 * handled point by point: the mean is drawn where at least one run has a value, and the band
 * only where at least two do. Past the end of the shorter seeds the line continues as the
 * longest run alone, without a band.
 */

import { Series, smoothed, smooths, type Smoothing } from './series.ts';

/** What the band shows: the mean ± the (population) standard deviation, or the min and max. */
export type BandKind = 'std' | 'minmax';

/** One run's curve: values `y` at ascending `x`. Non-finite values (or null) are gaps. */
export interface Curve {
	x: ArrayLike<number>;
	y: ArrayLike<number | null>;
}

export interface Aggregate {
	/** The common grid. */
	x: Float64Array;
	/** Mean of the runs with a value at each grid point; NaN where none has one. */
	mean: Float32Array;
	/** The band's ends; NaN where fewer than two runs have a value. */
	lo: Float32Array;
	hi: Float32Array;
	/** Runs with a value at each grid point. */
	count: Uint32Array;
}

/** Most points of an interpolated grid. */
export const GRID_MAX = 1024;

const lengthOf = (c: Curve) => Math.min(c.x.length, c.y.length);

function sameX(a: Curve, b: Curve): boolean {
	const n = lengthOf(a);
	if (n !== lengthOf(b)) return false;
	for (let i = 0; i < n; i++) if (a.x[i] !== b.x[i]) return false;
	return true;
}

/**
 * The x grid the curves are aggregated on (see the module's comment). `size` overrides the
 * number of points of an interpolated grid. Empty when no curve has a point.
 */
export function commonGrid(curves: readonly Curve[], size?: number): Float64Array {
	const present = curves.filter((c) => lengthOf(c) > 0);
	if (!present.length) return new Float64Array(0);
	const first = present[0];
	if (present.every((c) => sameX(c, first)))
		return Float64Array.from({ length: lengthOf(first) }, (_, i) => first.x[i]);
	let lo = Infinity;
	let hi = -Infinity;
	let longest = 0;
	for (const c of present) {
		const n = lengthOf(c);
		lo = Math.min(lo, c.x[0]);
		hi = Math.max(hi, c.x[n - 1]);
		longest = Math.max(longest, n);
	}
	if (!(hi > lo)) return Float64Array.of(lo);
	const n = Math.max(2, Math.min(GRID_MAX, Math.round(size ?? longest)));
	const grid = new Float64Array(n);
	for (let i = 0; i < n; i++) grid[i] = lo + ((hi - lo) * i) / (n - 1);
	// Exactly the last x, whatever the rounding.
	grid[n - 1] = hi;
	return grid;
}

/**
 * `curve` at each point of `grid` (ascending), linearly interpolated between its points; NaN
 * outside its span, and between two points where either is a gap. Written into `out` when given.
 */
export function interpolate(
	curve: Curve,
	grid: ArrayLike<number>,
	out: Float64Array = new Float64Array(grid.length)
): Float64Array {
	const n = lengthOf(curve);
	const xs = curve.x;
	const value = (i: number) => {
		const v = curve.y[i];
		return v == null || !Number.isFinite(v) ? NaN : v;
	};
	let j = 0;
	for (let g = 0; g < grid.length; g++) {
		const x = grid[g];
		out[g] = NaN;
		if (!n || x < xs[0] || x > xs[n - 1]) continue;
		// The last point at or before x.
		while (j + 1 < n && xs[j + 1] <= x) j++;
		if (xs[j] === x || j + 1 >= n) {
			out[g] = xs[j] === x ? value(j) : NaN;
			continue;
		}
		const a = value(j);
		const b = value(j + 1);
		const w = xs[j + 1] - xs[j];
		out[g] = w > 0 ? a + ((b - a) * (x - xs[j])) / w : a;
	}
	return out;
}

/**
 * The mean and band of `curves` on their common grid (or on `grid`, when given). Values are
 * accumulated in double precision with Welford's method, so a band stays exact for curves with
 * a large offset and a small spread (a loss of 1e4 ± 1e-3).
 */
export function aggregate(
	curves: readonly Curve[],
	kind: BandKind,
	grid: Float64Array = commonGrid(curves)
): Aggregate {
	const n = grid.length;
	const count = new Uint32Array(n);
	const mean = new Float64Array(n);
	const m2 = new Float64Array(n);
	const min = new Float64Array(n).fill(Infinity);
	const max = new Float64Array(n).fill(-Infinity);
	const scratch = new Float64Array(n);
	for (const c of curves) {
		interpolate(c, grid, scratch);
		for (let i = 0; i < n; i++) {
			const v = scratch[i];
			if (!Number.isFinite(v)) continue;
			const k = ++count[i];
			const d = v - mean[i];
			mean[i] += d / k;
			m2[i] += d * (v - mean[i]);
			if (v < min[i]) min[i] = v;
			if (v > max[i]) max[i] = v;
		}
	}
	const out: Aggregate = {
		x: grid,
		mean: new Float32Array(n).fill(NaN),
		lo: new Float32Array(n).fill(NaN),
		hi: new Float32Array(n).fill(NaN),
		count
	};
	for (let i = 0; i < n; i++) {
		const k = count[i];
		if (!k) continue;
		out.mean[i] = mean[i];
		if (k < 2) continue;
		if (kind === 'minmax') {
			out.lo[i] = min[i];
			out.hi[i] = max[i];
		} else {
			const sd = Math.sqrt(m2[i] / k);
			out.lo[i] = mean[i] - sd;
			out.hi[i] = mean[i] + sd;
		}
	}
	return out;
}

/**
 * Runs' curves as one mean line to draw (`PlotSeries`): `series` is the mean of the curves as
 * logged (what a smoothed line fades in behind it), `values` the mean of the smoothed curves,
 * and the band their spread. Each curve is smoothed before averaging, so the band is the
 * spread between runs and not the noise along each.
 */
export function meanLine(
	curves: readonly Series[],
	smoothing: Smoothing,
	kind: BandKind
): { series: Series; values: Float32Array; band: { lo: Float32Array; hi: Float32Array } } {
	const grid = commonGrid(curves);
	const raw = aggregate(curves, kind, grid);
	const series = new Series(raw.mean, grid);
	if (!smooths(smoothing)) return { series, values: series.y, band: { lo: raw.lo, hi: raw.hi } };
	const smooth = aggregate(
		curves.map((c) => ({ x: c.x, y: smoothed(c, smoothing) })),
		kind,
		grid
	);
	return { series, values: smooth.mean, band: { lo: smooth.lo, hi: smooth.hi } };
}
