import { describe, expect, it } from 'vitest';
import {
	clampSmoothing,
	ema,
	gaussian,
	normalSmoothing,
	pointAt,
	runningAverage,
	Series,
	smooth,
	smoothed,
	smooths,
	snapStep,
	timeWeightedEma
} from './series.ts';

/** Flock's sampling (Snapshot.build in app/api/src/api/dashboard.py). */
function sampleIndices(raw: number, n: number) {
	if (n >= raw) return Array.from({ length: raw }, (_, i) => i);
	return Array.from({ length: n }, (_, i) =>
		Math.floor((i * (raw - 1) + Math.floor((n - 1) / 2)) / (n - 1))
	);
}

function regular(n: number, raw: number, x0: number, dx: number) {
	return new Series(
		Float32Array.from({ length: n }, (_, i) => i),
		null,
		{ raw, x0, dx }
	);
}

describe('regular series', () => {
	it.each([
		[500, 2400, 0, 1],
		[37, 37, 0, 50],
		[500, 613, 10, 5],
		[7, 1000, 0, 3]
	])('n=%i of %i points from %i every %i', (n, raw, x0, dx) => {
		const s = regular(n, raw, x0, dx);
		const expected = sampleIndices(raw, n).map((k) => x0 + dx * k);
		for (let i = 0; i < n; i++) {
			expect(s.stepAt(i)).toBe(expected[i]);
			expect(s.indexNear(expected[i])).toBe(i);
		}
		expect(Array.from(s.x)).toEqual(expected);
		expect(s.lastStep).toBe(x0 + dx * (raw - 1));
	});

	it('clamps', () => {
		const s = regular(10, 100, 0, 1);
		expect(s.indexNear(-50)).toBe(0);
		expect(s.indexNear(10_000)).toBe(9);
	});
});

describe('reading out at a step', () => {
	const sparse = new Series(Float32Array.from([1, 2, 3]), Float64Array.from([0, 500, 1000]));
	const once = new Series(Float32Array.from([7]), Float64Array.from([500]));

	it('reads a line at its nearest point within its span', () => {
		expect(pointAt(sparse, 437, 0)).toBe(1);
		expect(pointAt(sparse, 800, 0)).toBe(2);
		expect(pointAt(regular(10, 100, 0, 1), 50, 0)).toBe(5);
	});

	it('reads past its ends, and a lone point, only within the tolerance', () => {
		expect(pointAt(sparse, 1010, 5)).toBe(-1);
		expect(pointAt(sparse, 1004, 5)).toBe(2);
		// Logged once: a pointer is never exactly on its step.
		expect(pointAt(once, 503.7, 0)).toBe(-1);
		expect(pointAt(once, 503.7, 5)).toBe(0);
		expect(pointAt(new Series(new Float32Array(0), new Float64Array(0)), 0, 5)).toBe(-1);
	});

	it('snaps to the nearest logged step of any line', () => {
		expect(snapStep([sparse, once], 480)).toBe(500);
		expect(snapStep([sparse], 260)).toBe(500);
		expect(snapStep([regular(10, 100, 0, 1)], 41)).toBe(44);
		expect(snapStep([], 41.5)).toBe(41.5);
	});
});

describe('live appends', () => {
	it('drops points it already has and keeps extents current', () => {
		const s = new Series(Float32Array.from([1, 2]), Float64Array.from([0, 1]));
		expect(s.append([1, 2, 3], [9, 3, -4])).toBe(true);
		expect(Array.from(s.x)).toEqual([0, 1, 2, 3]);
		expect(Array.from(s.y)).toEqual([1, 2, 3, -4]);
		expect([s.min, s.max]).toEqual([-4, 3]);
		expect(s.append([3], [5])).toBe(false);
	});

	it('appends to a regular snapshot series', () => {
		const s = regular(3, 5, 0, 10);
		s.append([50, 60], [7, 8]);
		expect(Array.from(s.x)).toEqual([0, 20, 40, 50, 60]);
		expect(s.indexNear(58)).toBe(4);
	});

	it('halves long live series but keeps the newest point', () => {
		const s = new Series(new Float32Array(0), new Float64Array(0));
		const steps = Array.from({ length: Series.MAX_POINTS + 1 }, (_, i) => i);
		s.append(steps, steps);
		expect(s.n).toBeLessThanOrEqual(Series.MAX_POINTS / 2 + 1);
		expect(s.lastStep).toBe(Series.MAX_POINTS);
	});

	it('invalidates cached smoothing', () => {
		const s = new Series(Float32Array.from([0, 0]), Float64Array.from([0, 1]));
		const ema5 = { type: 'ema' as const, param: 0.5 };
		const before = smoothed(s, ema5);
		expect(smoothed(s, ema5)).toBe(before);
		expect(smoothed(s, { type: 'twema', param: 0.5 })).not.toBe(before);
		s.append([2], [10]);
		const after = smoothed(s, ema5);
		expect(after).not.toBe(before);
		expect(after.length).toBe(3);
	});
});

describe('ema', () => {
	it('is the identity at weight 0', () => {
		const v = new Float32Array([1, 5, 2]);
		expect(ema(v, 0)).toBe(v);
	});

	it('is debiased, so a constant series stays constant', () => {
		for (const x of ema(new Float32Array([4, 4, 4, 4]), 0.9)) expect(x).toBeCloseTo(4, 5);
	});

	it('skips missing values', () => {
		const out = ema(new Float32Array([2, NaN, 2]), 0.5);
		expect(Number.isNaN(out[1])).toBe(true);
		expect(out[2]).toBeCloseTo(2, 5);
	});
});

describe('non-finite values', () => {
	it('are left out of the extent, so one inf does not swallow the domain', () => {
		const s = new Series(Float32Array.from([30, Infinity, 20, NaN, -Infinity, 25]), null);
		expect([s.min, s.max]).toEqual([20, 30]);
		s.append([6, 7], [Infinity, 10]);
		expect([s.min, s.max]).toEqual([10, 30]);
	});

	it('leave an empty extent when no value is finite', () => {
		const s = new Series(Float32Array.from([Infinity, NaN]), null);
		expect([s.min, s.max]).toEqual([Infinity, -Infinity]);
	});

	it('are gaps in the smoothed line and do not feed the average', () => {
		const out = ema(new Float32Array([2, Infinity, 2, -Infinity, 2]), 0.5);
		expect(Number.isNaN(out[1])).toBe(true);
		expect(Number.isNaN(out[3])).toBe(true);
		expect(out[2]).toBeCloseTo(2, 5);
		expect(out[4]).toBeCloseTo(2, 5);
	});
});

describe('smoothing methods', () => {
	const xs = (...v: number[]) => Float64Array.from(v);
	const ys = (...v: number[]) => Float32Array.from(v);

	it('leave the line as it is when they would not change it', () => {
		const v = ys(1, 5, 2);
		const x = xs(0, 1, 2);
		expect(smooth(x, v, { type: 'none', param: 0 })).toBe(v);
		expect(smooth(x, v, { type: 'ema', param: 0 })).toBe(v);
		expect(smooth(x, v, { type: 'average', param: 1 })).toBe(v);
		expect(Array.from(smoothed(new Series(v, x), { type: 'twema', param: 0 }))).toEqual([1, 5, 2]);
	});

	it('time weighted EMA matches the EMA on evenly logged steps', () => {
		const v = ys(3, 1, 4, 1, 5, 9, 2, 6);
		const even = timeWeightedEma(xs(0, 10, 20, 30, 40, 50, 60, 70), v, 0.7);
		const plain = ema(v, 0.7);
		for (let i = 0; i < v.length; i++) expect(even[i]).toBeCloseTo(plain[i], 5);
	});

	it('time weighted EMA decays more across a longer gap', () => {
		// The same values; the last point comes after a gap of five average steps.
		const v = ys(0, 0, 0, 0, 10);
		const even = timeWeightedEma(xs(0, 1, 2, 3, 4), v, 0.8);
		const gap = timeWeightedEma(xs(0, 1, 2, 3, 8), v, 0.8);
		expect(gap[4]).toBeGreaterThan(even[4]);
	});

	it('running average is the mean of the last window points, skipping gaps', () => {
		const out = runningAverage(ys(1, 2, NaN, 3, 4, 5), 3);
		expect(Array.from(out).map((v) => (Number.isNaN(v) ? null : v))).toEqual([
			1,
			1.5,
			null,
			2,
			3,
			4
		]);
	});

	it('gaussian keeps a constant line and is centered, in points', () => {
		for (const v of gaussian(ys(2, 2, 2, 2, 2), 1.5)) expect(v).toBeCloseTo(2, 5);
		// A straight line is unchanged in the middle, where the kernel is symmetric.
		expect(gaussian(ys(0, 1, 2, 3, 4, 5, 6), 0.8)[3]).toBeCloseTo(3, 4);
		// A spike is spread to its neighbours, but no further than 3 sigma points.
		const spike = gaussian(ys(0, 0, 0, 7, 0, 0, 0), 0.5);
		expect(spike[3]).toBeLessThan(7);
		expect(spike[2]).toBeGreaterThan(0);
		expect(spike[1]).toBe(0);
		// Sigma counts points, whatever the steps between them.
		const v = ys(3, 1, 4, 1, 5, 9, 2, 6);
		expect(smooth(xs(0, 1, 2, 3, 4, 5, 6, 7), v, { type: 'gaussian', param: 2 })).toEqual(
			smooth(xs(0, 10, 500, 501, 9000, 9001, 9002, 20000), v, { type: 'gaussian', param: 2 })
		);
	});

	it('all skip non-finite points', () => {
		const v = ys(2, Infinity, 2, NaN, 2);
		const x = xs(0, 1, 2, 3, 4);
		for (const type of ['ema', 'twema', 'average', 'gaussian'] as const) {
			const out = smooth(x, v, { type, param: type === 'average' ? 3 : 0.5 });
			expect(Number.isNaN(out[1]) && Number.isNaN(out[3])).toBe(true);
			for (const i of [0, 2, 4]) expect(out[i]).toBeCloseTo(2, 5);
		}
	});

	it('clamp their parameter to what each method takes', () => {
		expect(clampSmoothing('ema', 2)).toBe(0.99);
		expect(clampSmoothing('average', 0)).toBe(1);
		expect(clampSmoothing('average', 4.4)).toBe(4);
		expect(clampSmoothing('gaussian', 1000)).toBe(100);
		expect(smooths({ type: 'gaussian', param: 0.1 })).toBe(true);
		expect(normalSmoothing({ type: 'twema', param: 0 })).toEqual({ type: 'none', param: 0 });
		expect(normalSmoothing({ type: 'ema', param: 0.3 })).toEqual({ type: 'ema', param: 0.3 });
	});
});
