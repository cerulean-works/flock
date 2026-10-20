import { describe, expect, it } from 'vitest';
import { computeDomain, type DomainOptions, type PlotSeries } from './engine.ts';
import { ema, Series } from './series.ts';

function plot(steps: number[], values: number[], smoothing = 0): PlotSeries {
	const series = new Series(Float32Array.from(values), Float64Array.from(steps));
	return { row: 0, color: '#000', series, values: ema(series.y, smoothing) };
}

const opts = (o: Partial<DomainOptions> = {}): DomainOptions => ({
	zoom: null,
	log: false,
	ignoreOutliers: false,
	...o
});

// A perplexity-like curve: a spike early on, then values between 30 and 85.
const perplexity = plot([0, 20, 40, 60, 80, 100, 120], [900, 300, 85, 60, 45, 30, 28]);

describe('computeDomain zoom', () => {
	it('fits y to what is inside the selected range, not the neighbours outside it', () => {
		const d = computeDomain([perplexity], opts({ zoom: [40, 105] }));
		expect([d.x0, d.x1]).toEqual([40, 105]);
		// Inside: 85..30, plus the line at x=105, which is 29.5. Padding is a few percent.
		expect(d.y1).toBeGreaterThan(85);
		expect(d.y1).toBeLessThan(90);
		expect(d.y0).toBeLessThan(29.5);
		expect(d.y0).toBeGreaterThan(25);
	});

	it('includes where the line crosses the frame edges', () => {
		// Between steps 20 (300) and 40 (85), the line at 30 is at 192.5.
		const d = computeDomain([perplexity], opts({ zoom: [30, 105] }));
		expect(d.y1).toBeGreaterThan(192.5);
		expect(d.y1).toBeLessThan(300);
	});

	it('fits a range between two points to the interpolated line', () => {
		const d = computeDomain([perplexity], opts({ zoom: [45, 55] }));
		// 85 → 60 between 40 and 60: 78.75 at 45, 66.25 at 55.
		expect(d.y0).toBeLessThan(66.25);
		expect(d.y0).toBeGreaterThan(60);
		expect(d.y1).toBeGreaterThan(78.75);
		expect(d.y1).toBeLessThan(85);
	});

	it('also fits the smoothed line, which carries earlier values into the range', () => {
		const p = plot(Array.from(perplexity.series.x), Array.from(perplexity.series.y), 0.6);
		const d = computeDomain([p], opts({ zoom: [40, 105] }));
		let peak = -Infinity;
		for (let i = 2; i <= 5; i++) peak = Math.max(peak, p.values[i]);
		expect(peak).toBeGreaterThan(85);
		expect(d.y1).toBeGreaterThan(peak);
	});

	it('crosses the edges in log space when the y axis is log', () => {
		const p = plot([0, 10], [1, 100]);
		const d = computeDomain([p], opts({ zoom: [5, 6], log: true }));
		// log10 goes 0 → 2, so 1 at x=5 and 1.2 at x=6.
		expect(d.y0).toBeLessThan(1);
		expect(d.y0).toBeGreaterThan(0.9);
		expect(d.y1).toBeGreaterThan(1.2);
		expect(d.y1).toBeLessThan(1.3);
	});
});

describe('computeDomain x padding', () => {
	it('keeps the first and last points off the frame when unzoomed', () => {
		const p = plot([500, 600, 1000], [1, 2, 3]);
		const d = computeDomain([p], opts());
		// 2% of the 500-step span on each side.
		expect(d.x0).toBeCloseTo(490, 9);
		expect(d.x1).toBeCloseTo(1010, 9);
	});

	it('starts a step axis at zero when the data starts near it', () => {
		const d = computeDomain([plot([5, 50, 100], [1, 2, 3])], opts());
		expect(d.x0).toBeLessThan(0);
		expect(d.x0).toBeGreaterThan(-5);
		expect(d.xTicks.values[0]).toBe(0);
	});

	it('centres a single point', () => {
		const d = computeDomain([plot([7], [1])], opts());
		expect((d.x0 + d.x1) / 2).toBeCloseTo(7, 9);
	});
});

describe('computeDomain yRange', () => {
	const p = plot([0, 1, 2], [10, 20, 30]);

	it('uses fixed ends exactly and fits the other end to the data', () => {
		const lo = computeDomain([p], opts({ yRange: [0, null] }));
		expect(lo.y0).toBe(0);
		expect(lo.y1).toBeGreaterThan(30);
		expect(lo.y1).toBeLessThan(32);
		const hi = computeDomain([p], opts({ yRange: [null, 25] }));
		expect(hi.y1).toBe(25);
		expect(hi.y0).toBeLessThan(10);
		const both = computeDomain([p], opts({ yRange: [5, 50] }));
		expect([both.y0, both.y1]).toEqual([5, 50]);
		expect(both.yTicks.values[0]).toBeGreaterThanOrEqual(5);
		expect(both.yTicks.values.at(-1)).toBeLessThanOrEqual(50);
	});

	it('applies with zoom and ignores a null range', () => {
		const d = computeDomain([p], opts({ zoom: [1, 2], yRange: [null, 100] }));
		expect(d.y1).toBe(100);
		expect(d.y0).toBeLessThan(20);
		expect(computeDomain([p], opts({ yRange: null }))).toEqual(computeDomain([p], opts()));
	});

	it('keeps the domain drawable when a fixed end is past the data', () => {
		const d = computeDomain([p], opts({ yRange: [100, null] }));
		expect(d.y0).toBe(100);
		expect(d.y1).toBeGreaterThan(100);
	});

	it('converts fixed ends to log10 and ignores non-positive ones', () => {
		const d = computeDomain([p], opts({ log: true, yRange: [1, 1000] }));
		expect([d.y0, d.y1]).toEqual([0, 3]);
		expect(d.yTicks.values).toEqual([0, 1, 2, 3]);
		const auto = computeDomain([p], opts({ log: true }));
		const d2 = computeDomain([p], opts({ log: true, yRange: [0, 1000] }));
		expect(d2.y1).toBe(3);
		expect(d2.y0).toBeLessThan(1);
		expect(d2.y0).toBeGreaterThan(auto.y0 - 0.2);
	});
});

describe('computeDomain with non-finite values', () => {
	// eval/ppl = exp(eval/loss): a loss spike to ~500 overflows float32 (the snapshot's y type).
	const ppl = plot([0, 100, 200, 300, 400], [40, Infinity, 32, NaN, 28]);
	const healthy = plot([0, 100, 200, 300, 400], [45, 38, 33, 30, 29]);

	it('fits the finite values rather than falling back to 0..1', () => {
		for (const o of [opts(), opts({ ignoreOutliers: true })]) {
			const d = computeDomain([ppl, healthy], o);
			expect(d.y0).toBeGreaterThan(20);
			expect(d.y0).toBeLessThan(29);
			expect(d.y1).toBeGreaterThan(44);
			expect(d.y1).toBeLessThan(60);
		}
		// Zoomed past step 0: up to where `healthy` crosses the left edge (41.5 at step 50).
		const d = computeDomain([ppl, healthy], opts({ zoom: [50, 450] }));
		expect(d.y0).toBeLessThan(28);
		expect(d.y0).toBeGreaterThan(20);
		expect(d.y1).toBeGreaterThan(41.5);
		expect(d.y1).toBeLessThan(50);
	});

	it('fits the finite values on a log axis', () => {
		const d = computeDomain([ppl], opts({ log: true }));
		expect(d.y0).toBeLessThan(Math.log10(28));
		expect(d.y1).toBeGreaterThan(Math.log10(40));
		expect(d.y1).toBeLessThan(2);
	});

	it('fits the smoothed line, which skips the inf too', () => {
		const p = plot(Array.from(ppl.series.x), Array.from(ppl.series.y), 0.6);
		const d = computeDomain([p], opts());
		expect(d.y1).toBeLessThan(60);
		expect(d.y0).toBeGreaterThan(20);
	});

	it('falls back to the empty plot domain only when nothing is finite', () => {
		const d = computeDomain([plot([0, 1], [Infinity, NaN])], opts());
		expect(d).toEqual(computeDomain([], opts({ zoom: [d.x0, d.x1] })));
	});
});
