import { describe, expect, it } from 'vitest';
import { FORECAST_MIN_POINTS, fitPowerLaw, forecastOf, powerLaw } from './forecast.ts';
import { Series } from './series.ts';

/** A run of `n` points from step 1 to `last`, logging `f`. */
function run(n: number, last: number, f: (t: number) => number) {
	const xs = Float64Array.from({ length: n }, (_, i) => 1 + ((last - 1) * i) / (n - 1));
	return { xs, ys: Float32Array.from(xs, f) };
}

describe('fitPowerLaw', () => {
	it('recovers a power law from its points', () => {
		const truth = { a: 0.5, b: 3, c: 0.4 };
		const { xs, ys } = run(200, 1000, (t) => powerLaw(truth, t));
		const fit = fitPowerLaw(xs, ys)!;
		expect(fit.a).toBeCloseTo(0.5, 2);
		expect(fit.c).toBeCloseTo(0.4, 1);
		for (const t of [1000, 2000, 5000]) expect(powerLaw(fit, t)).toBeCloseTo(powerLaw(truth, t), 3);
	});

	it('recovers it through noise and a warmup unlike the curve', () => {
		const truth = { a: 1.8, b: 6, c: 0.6 };
		let seed = 7;
		const noise = () => ((seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5) * 0.004;
		// The first 10% of the run is warmup, nothing like the curve after it.
		const { xs, ys } = run(400, 4000, (t) => (t < 300 ? 0.5 : powerLaw(truth, t) + noise()));
		const fit = fitPowerLaw(xs, ys)!;
		expect(Math.abs(powerLaw(fit, 8000) - powerLaw(truth, 8000))).toBeLessThan(0.01);
	});

	it('gives none for too few points', () => {
		const { xs, ys } = run(FORECAST_MIN_POINTS - 1, 100, (t) => 1 + 2 / t);
		expect(fitPowerLaw(xs, ys)).toBeNull();
		// Points that aren't finite don't count.
		const more = run(FORECAST_MIN_POINTS + 2, 100, (t) => 1 + 2 / t);
		more.ys[3] = NaN;
		more.ys[4] = Infinity;
		more.ys[5] = NaN;
		expect(fitPowerLaw(more.xs, more.ys)).toBeNull();
		expect(fitPowerLaw(run(FORECAST_MIN_POINTS, 100, (t) => 1 + 2 / t).xs, more.ys)).toBeNull();
	});

	it('gives none for a curve that is increasing', () => {
		const { xs, ys } = run(100, 1000, (t) => 0.9 - 0.5 * t ** -0.5);
		expect(fitPowerLaw(xs, ys)).toBeNull();
		const up = run(100, 1000, (t) => 0.01 * t);
		expect(fitPowerLaw(up.xs, up.ys)).toBeNull();
	});
});

describe('forecastOf', () => {
	it('continues a series from its last step to the horizon, memoised', () => {
		const { xs, ys } = run(100, 500, (t) => 2 + 4 * t ** -0.5);
		const s = new Series(ys, xs);
		const line = forecastOf(s, 1000)!;
		expect(line.firstStep).toBe(500);
		expect(line.lastStep).toBe(1000);
		expect(line.y[line.n - 1]).toBeCloseTo(2 + 4 * 1000 ** -0.5, 3);
		expect(forecastOf(s, 1000)).toBe(line);
		expect(forecastOf(s, 400)).toBeNull();
		s.append([510], [2 + 4 * 510 ** -0.5]);
		expect(forecastOf(s, 1000)!.firstStep).toBe(510);
	});
});
