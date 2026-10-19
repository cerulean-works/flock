import { describe, expect, it } from 'vitest';
import { logTicks, niceDomain, niceTicks, timeTicks } from './scale.ts';

describe('niceTicks', () => {
	it('uses 1/2/5 steps inside the range', () => {
		const t = niceTicks(0, 2600, 5);
		expect(t.step).toBe(500);
		expect(t.values).toEqual([0, 500, 1000, 1500, 2000, 2500]);
	});

	it('handles tiny ranges without float noise', () => {
		const t = niceTicks(0.1, 0.3, 4);
		expect(t.values).toEqual([0.1, 0.15, 0.2, 0.25, 0.3]);
	});
});

describe('timeTicks', () => {
	it('steps in round minutes and hours', () => {
		expect(timeTicks(0, 1000, 4).step).toBe(300);
		expect(timeTicks(0, 5 * 3600, 5).step).toBe(3600);
	});

	it('steps in whole days for long runs', () => {
		expect(timeTicks(0, 20 * 86_400, 4).step).toBe(5 * 86_400);
	});

	it('falls back to decimal ticks under a second', () => {
		expect(timeTicks(0, 2, 4).values).toEqual([0, 0.5, 1, 1.5, 2]);
	});
});

describe('niceDomain', () => {
	it('pads a flat series so it is still drawable', () => {
		const [lo, hi] = niceDomain(5, 5);
		expect(lo).toBeLessThan(5);
		expect(hi).toBeGreaterThan(5);
	});
});

describe('logTicks', () => {
	it('places ticks on powers of ten', () => {
		expect(logTicks(-6.2, -3.1).values).toEqual([-6, -5, -4]);
	});
});
