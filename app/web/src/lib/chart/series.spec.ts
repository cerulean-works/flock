import { describe, expect, it } from 'vitest';
import { ema, Series, smoothed } from './series.ts';

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

	it('clamps and checks coverage', () => {
		const s = regular(10, 100, 0, 1);
		expect(s.indexNear(-50)).toBe(0);
		expect(s.indexNear(10_000)).toBe(9);
		expect(s.covers(50)).toBe(true);
		expect(s.covers(300)).toBe(false);
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
		const before = smoothed(s, 0.5);
		expect(smoothed(s, 0.5)).toBe(before);
		s.append([2], [10]);
		const after = smoothed(s, 0.5);
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
