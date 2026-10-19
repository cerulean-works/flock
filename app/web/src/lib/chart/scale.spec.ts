import { describe, expect, it } from 'vitest';
import { logTicks, niceDomain, niceTicks, timeTicks, wallTicks } from './scale.ts';

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

describe('wallTicks', () => {
	it("falls on round times of the viewer's clock", () => {
		// 2026-10-20 09:10 to 13:50 UTC, seen from UTC+5:30 (an offset of -330 minutes).
		const lo = Date.UTC(2026, 9, 20, 9, 10) / 1000;
		const t = wallTicks(lo, lo + 280 * 60, 4, -330);
		expect(t.step).toBe(3600);
		// 15:00, 16:00... local is 9:30, 10:30... UTC.
		expect(t.values.map((v) => new Date(v * 1000).toISOString().slice(11, 16))).toEqual([
			'09:30',
			'10:30',
			'11:30',
			'12:30',
			'13:30'
		]);
	});
});

describe('niceDomain', () => {
	it('pads a flat series so it is still drawable', () => {
		const [lo, hi] = niceDomain(5, 5);
		expect(lo).toBeLessThan(5);
		expect(hi).toBeGreaterThan(5);
	});

	it('keeps peaks and troughs off the frame', () => {
		const [lo, hi] = niceDomain(30, 85);
		expect(lo).toBeLessThan(30);
		expect(hi).toBeGreaterThan(85);
		expect(hi - 85).toBeCloseTo(30 - lo, 9);
	});
});

describe('logTicks', () => {
	it('places ticks on powers of ten', () => {
		expect(logTicks(-6.2, -3.1).values).toEqual([-6, -5, -4]);
	});
});
