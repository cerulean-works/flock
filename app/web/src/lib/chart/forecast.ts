import { Series } from './series.ts';

/**
 * Where a run's loss is heading: a saturating power law fitted to its points, the usual shape
 * of a training loss, `L(t) = a + b·t^(-c)` with `b`, `c` > 0.
 */
export interface PowerLaw {
	a: number;
	b: number;
	c: number;
}

/** Fewer points than this get no forecast: too few to tell a trend from noise. */
export const FORECAST_MIN_POINTS = 20;
/** Share of a run left out of the fit at its start, which skips warmup. */
const WARMUP = 0.1;
/** Candidate asymptotes: this many, spaced evenly in log of their distance below the minimum. */
const GRID = 48;
/** Steps of the search that refines the best candidate. */
const REFINE = 40;
/** Points a forecast is drawn with. */
const POINTS = 64;

export const powerLaw = ({ a, b, c }: PowerLaw, t: number) => a + b * t ** -c;

/**
 * The power law nearest the points (`xs` ascending), or null for none: fewer than
 * `FORECAST_MIN_POINTS` finite points, or a fit that comes out increasing.
 *
 * Fitted after the first 10% of the run, to the points with positive x. The asymptote `a` is
 * searched below the points' minimum, a grid then a finer search around its best; for each
 * `a`, `b` and `c` are a least squares line through `log(L - a)` against `log t`. The `a` with
 * the lowest squared error in `L` wins. Cheap, deterministic, and dependency free.
 */
export function fitPowerLaw(xs: ArrayLike<number>, ys: ArrayLike<number>): PowerLaw | null {
	const n = Math.min(xs.length, ys.length);
	let count = 0;
	let first = NaN;
	let last = NaN;
	for (let i = 0; i < n; i++) {
		if (!Number.isFinite(ys[i]) || !Number.isFinite(xs[i])) continue;
		if (!count) first = xs[i];
		last = xs[i];
		count++;
	}
	if (count < FORECAST_MIN_POINTS) return null;
	const from = first + WARMUP * (last - first);
	const t: number[] = [];
	const l: number[] = [];
	for (let i = 0; i < n; i++) {
		const x = xs[i];
		const y = ys[i];
		if (!Number.isFinite(y) || !Number.isFinite(x) || x < from || x <= 0) continue;
		t.push(x);
		l.push(y);
	}
	if (t.length < 3) return null;
	const logT = t.map(Math.log);
	let min = Infinity;
	let max = -Infinity;
	for (const y of l) {
		if (y < min) min = y;
		if (y > max) max = y;
	}
	const span = max - min || Math.abs(min) || 1;

	/** The least squares fit with asymptote `min - gap`, and its squared error. */
	function fitBelow(gap: number): (PowerLaw & { error: number }) | null {
		const a = min - gap;
		let su = 0;
		let sv = 0;
		let suu = 0;
		let suv = 0;
		const m = t.length;
		for (let i = 0; i < m; i++) {
			const u = logT[i];
			const v = Math.log(l[i] - a);
			su += u;
			sv += v;
			suu += u * u;
			suv += u * v;
		}
		const den = m * suu - su * su;
		if (!(Math.abs(den) > 0)) return null;
		const slope = (m * suv - su * sv) / den;
		const fit = { a, b: Math.exp((sv - slope * su) / m), c: -slope };
		let error = 0;
		for (let i = 0; i < m; i++) error += (l[i] - powerLaw(fit, t[i])) ** 2;
		return Number.isFinite(error) ? { ...fit, error } : null;
	}

	// Gaps from 1e-4 to 1e2 spans below the minimum.
	const gapAt = (k: number) => span * 10 ** (-4 + (6 * k) / (GRID - 1));
	let best: (PowerLaw & { error: number }) | null = null;
	let at = -1;
	for (let k = 0; k < GRID; k++) {
		const fit = fitBelow(gapAt(k));
		if (fit && (!best || fit.error < best.error)) {
			best = fit;
			at = k;
		}
	}
	if (!best) return null;
	// Golden section search between the best candidate's neighbours, in log of the gap.
	let lo = Math.log(gapAt(Math.max(0, at - 1)));
	let hi = Math.log(gapAt(Math.min(GRID - 1, at + 1)));
	const ratio = (Math.sqrt(5) - 1) / 2;
	for (let i = 0; i < REFINE; i++) {
		const p = hi - ratio * (hi - lo);
		const q = lo + ratio * (hi - lo);
		const fp = fitBelow(Math.exp(p))?.error ?? Infinity;
		const fq = fitBelow(Math.exp(q))?.error ?? Infinity;
		if (fp < fq) hi = q;
		else lo = p;
	}
	const refined = fitBelow(Math.exp((lo + hi) / 2));
	if (refined && refined.error < best.error) best = refined;
	if (!(best.b > 0 && best.c > 0)) return null;
	return { a: best.a, b: best.b, c: best.c };
}

const fits = new WeakMap<Series, { version: number; fit: PowerLaw | null }>();
const lines = new WeakMap<Series, { version: number; to: number; line: Series | null }>();

/** The power law fitted to a series' points, memoised until the series changes. */
export function fitOf(series: Series): PowerLaw | null {
	const hit = fits.get(series);
	if (hit?.version === series.version) return hit.fit;
	const fit = fitPowerLaw(series.x, series.y);
	fits.set(series, { version: series.version, fit });
	return fit;
}

/**
 * A series' forecast: its fitted power law from its last point to `to`, or null without a fit
 * or when `to` is not past its last point. Memoised until the series or `to` changes.
 */
export function forecastOf(series: Series, to: number): Series | null {
	const hit = lines.get(series);
	if (hit?.version === series.version && hit.to === to) return hit.line;
	const fit = series.n ? fitOf(series) : null;
	const from = series.lastStep;
	let line: Series | null = null;
	if (fit && to > from) {
		const x = new Float64Array(POINTS);
		const y = new Float32Array(POINTS);
		for (let i = 0; i < POINTS; i++) {
			x[i] = from + ((to - from) * i) / (POINTS - 1);
			y[i] = powerLaw(fit, x[i]);
		}
		line = new Series(y, x);
	}
	lines.set(series, { version: series.version, to, line });
	return line;
}
