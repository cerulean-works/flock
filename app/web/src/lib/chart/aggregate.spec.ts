import { describe, expect, it } from 'vitest';
import { aggregate, commonGrid, GRID_MAX, interpolate, meanLine, type Curve } from './aggregate.ts';
import { NO_SMOOTHING, Series } from './series.ts';

const curve = (x: number[], y: (number | null)[]): Curve => ({ x, y });

describe('commonGrid', () => {
	it('is empty without points', () => {
		expect(commonGrid([])).toHaveLength(0);
		expect(commonGrid([curve([], [])])).toHaveLength(0);
	});

	it('keeps the x values every curve shares, without interpolating', () => {
		const a = curve([0, 10, 25], [1, 2, 3]);
		const b = curve([0, 10, 25], [4, 5, 6]);
		expect(Array.from(commonGrid([a, b]))).toEqual([0, 10, 25]);
	});

	it('ignores curves without points when deciding whether x values are shared', () => {
		const a = curve([0, 10, 25], [1, 2, 3]);
		expect(Array.from(commonGrid([a, curve([], [])]))).toEqual([0, 10, 25]);
	});

	it('spans the union of the spans, evenly, with as many points as the longest curve', () => {
		const a = curve([0, 50, 100], [1, 2, 3]);
		const b = curve([10, 20, 30, 40, 200], [1, 2, 3, 4, 5]);
		const grid = commonGrid([a, b]);
		expect(grid).toHaveLength(5);
		expect(Array.from(grid)).toEqual([0, 50, 100, 150, 200]);
	});

	it('takes a size, at least two points and at most GRID_MAX', () => {
		const a = curve([0, 1], [1, 2]);
		const b = curve([0, 2], [1, 2]);
		expect(commonGrid([a, b], 11)).toHaveLength(11);
		expect(commonGrid([a, b], 1)).toHaveLength(2);
		expect(commonGrid([a, b], 1e6)).toHaveLength(GRID_MAX);
	});

	it('ends exactly at the last x', () => {
		const grid = commonGrid([curve([0, 0.1], [1, 2]), curve([0, 0.3], [1, 2])], 7);
		expect(grid[grid.length - 1]).toBe(0.3);
		expect(grid[0]).toBe(0);
	});

	it('is a single point when every curve is at one x', () => {
		const grid = commonGrid([curve([5], [1]), curve([5, 5], [2, 3])]);
		expect(Array.from(grid)).toEqual([5]);
	});
});

describe('interpolate', () => {
	it('reads points exactly and interpolates linearly between them', () => {
		const c = curve([0, 10, 20], [0, 100, 50]);
		expect(Array.from(interpolate(c, [0, 5, 10, 15, 20]))).toEqual([0, 50, 100, 75, 50]);
	});

	it('never extrapolates past either end', () => {
		const c = curve([10, 20], [1, 2]);
		const out = interpolate(c, [0, 10, 20, 30]);
		expect(out[0]).toBeNaN();
		expect(out[1]).toBe(1);
		expect(out[2]).toBe(2);
		expect(out[3]).toBeNaN();
	});

	it('leaves gaps around missing and overflowed points', () => {
		const c = curve([0, 10, 20, 30], [1, null, Infinity, 4]);
		const out = interpolate(c, [0, 5, 10, 15, 25, 30]);
		expect(out[0]).toBe(1);
		expect(out[1]).toBeNaN();
		expect(out[2]).toBeNaN();
		expect(out[3]).toBeNaN();
		expect(out[4]).toBeNaN();
		expect(out[5]).toBe(4);
	});

	it('reads a single point only at its x', () => {
		const out = interpolate(curve([10], [3]), [0, 10, 20]);
		expect(out[0]).toBeNaN();
		expect(out[1]).toBe(3);
		expect(out[2]).toBeNaN();
	});

	it('writes into the buffer it is given', () => {
		const buf = new Float64Array(2);
		const out = interpolate(curve([0, 1], [1, 3]), [0, 0.5], buf);
		expect(out).toBe(buf);
		expect(Array.from(buf)).toEqual([1, 2]);
	});

	it('takes the later point where x repeats', () => {
		const out = interpolate(curve([0, 10, 10, 20], [0, 1, 5, 5]), [10, 15]);
		expect(out[0]).toBe(5);
		expect(out[1]).toBe(5);
	});
});

describe('aggregate', () => {
	it('is the mean ± the population standard deviation', () => {
		const a = curve([0, 1, 2], [1, 2, 3]);
		const b = curve([0, 1, 2], [3, 4, 5]);
		const agg = aggregate([a, b], 'std');
		expect(Array.from(agg.x)).toEqual([0, 1, 2]);
		expect(Array.from(agg.mean)).toEqual([2, 3, 4]);
		// Population std of {1, 3} is 1.
		expect(Array.from(agg.lo)).toEqual([1, 2, 3]);
		expect(Array.from(agg.hi)).toEqual([3, 4, 5]);
		expect(Array.from(agg.count)).toEqual([2, 2, 2]);
	});

	it('spans the min and max with minmax', () => {
		const runs = [curve([0, 1], [1, 10]), curve([0, 1], [2, 20]), curve([0, 1], [6, 30])];
		const agg = aggregate(runs, 'minmax');
		expect(Array.from(agg.mean)).toEqual([3, 20]);
		expect(Array.from(agg.lo)).toEqual([1, 10]);
		expect(Array.from(agg.hi)).toEqual([6, 30]);
	});

	it('draws the mean where one run has a value and the band only where two do', () => {
		// The second seed stopped at step 2.
		const long = curve([0, 1, 2, 3, 4], [1, 1, 1, 1, 1]);
		const short = curve([0, 1, 2], [3, 3, 3]);
		const agg = aggregate([long, short], 'std');
		expect(agg.x).toHaveLength(5);
		expect(Array.from(agg.count)).toEqual([2, 2, 2, 1, 1]);
		expect(Array.from(agg.mean)).toEqual([2, 2, 2, 1, 1]);
		expect(agg.lo[2]).toBe(1);
		expect(agg.hi[2]).toBe(3);
		expect(agg.lo[3]).toBeNaN();
		expect(agg.hi[4]).toBeNaN();
	});

	it('aligns runs sampled at different steps on one grid', () => {
		// Same line y = x, sampled at different steps: the mean is the line, the band empty.
		const a = curve([0, 40, 100], [0, 40, 100]);
		const b = curve([0, 25, 50, 75, 100], [0, 25, 50, 75, 100]);
		const agg = aggregate([a, b], 'std');
		expect(Array.from(agg.x)).toEqual([0, 25, 50, 75, 100]);
		for (let i = 0; i < agg.x.length; i++) {
			expect(agg.mean[i]).toBeCloseTo(agg.x[i], 5);
			expect(agg.hi[i] - agg.lo[i]).toBeCloseTo(0, 5);
		}
	});

	it('is NaN where no run has a value', () => {
		const a = curve([0, 10], [1, 1]);
		const b = curve([30, 40], [2, 2]);
		const agg = aggregate([a, b], 'std', Float64Array.of(0, 20, 40));
		expect(agg.mean[0]).toBe(1);
		expect(agg.mean[1]).toBeNaN();
		expect(agg.count[1]).toBe(0);
		expect(agg.mean[2]).toBe(2);
	});

	it('skips gaps rather than counting them', () => {
		const a = curve([0, 1], [1, NaN]);
		const b = curve([0, 1], [3, 5]);
		const agg = aggregate([a, b], 'std');
		expect(Array.from(agg.count)).toEqual([2, 1]);
		expect(agg.mean[1]).toBe(5);
		expect(agg.lo[1]).toBeNaN();
	});

	it('keeps a small spread on a large offset', () => {
		const a = curve([0], [10000.001]);
		const b = curve([0], [9999.999]);
		const agg = aggregate([a, b], 'std', Float64Array.of(0));
		// Float32 output rounds the ends, but they still straddle the mean.
		expect(agg.lo[0]).toBeLessThan(agg.hi[0]);
		expect(agg.mean[0]).toBeCloseTo(10000, 2);
	});

	it('is a single run, without a band', () => {
		const agg = aggregate([curve([0, 1], [2, 4])], 'minmax');
		expect(Array.from(agg.mean)).toEqual([2, 4]);
		expect(agg.lo[0]).toBeNaN();
		expect(agg.hi[1]).toBeNaN();
	});

	it('is empty without curves', () => {
		const agg = aggregate([], 'std');
		expect(agg.x).toHaveLength(0);
		expect(agg.mean).toHaveLength(0);
	});
});

describe('meanLine', () => {
	const run = (y: number[]) => new Series(Float32Array.from(y), Float64Array.from(y.keys()));

	it('is the runs’ mean with a ± std band, on their steps', () => {
		const line = meanLine([run([1, 2, 3]), run([3, 4, 5])], NO_SMOOTHING, 'std');
		expect([...line.series.x]).toEqual([0, 1, 2]);
		expect([...line.values]).toEqual([2, 3, 4]);
		expect([...line.band.lo]).toEqual([1, 2, 3]);
		expect([...line.band.hi]).toEqual([3, 4, 5]);
	});

	it('smooths each run before averaging, and keeps the logged mean behind it', () => {
		const smoothing = { type: 'ema', param: 0.9 } as const;
		const line = meanLine([run([0, 10, 0, 10]), run([10, 0, 10, 0])], smoothing, 'std');
		expect([...line.series.y]).toEqual([5, 5, 5, 5]);
		// Smoothed, the two runs are closer than as logged: a narrower band than ± 5.
		const width = line.band.hi[3] - line.band.lo[3];
		expect(width).toBeLessThan(10);
	});
});
