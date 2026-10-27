import { describe, expect, it } from 'vitest';
import { placeTip, Readout, tooltipPosition, tooltipRows, type ReadLine } from './readout.ts';
import { Series } from './series.ts';

function line(steps: number[], values: number[], color = '#000'): ReadLine {
	const series = new Series(Float32Array.from(values), Float64Array.from(steps));
	return { series, values: series.y, color };
}

describe('Readout', () => {
	const a = line([0, 10, 20], [1, 5, 3], 'a');
	const b = line([0, 10, 20], [2, 9, 4], 'b');
	const sparse = line([0, 500], [7, 8], 'c');
	const blown = line([0, 10, 20], [1, Infinity, NaN], 'd');

	it('reads every line at the step, sorted high to low', () => {
		const r = new Readout().read([a, b], 10, 1);
		expect(r.count).toBe(2);
		expect(r.order.map((k) => r.value[k])).toEqual([9, 5]);
		expect(r.order.map((k) => r.line[k])).toEqual([1, 0]);
		expect(Array.from(r.step.subarray(0, 2))).toEqual([10, 10]);
	});

	it('skips non-finite values: no marker or row for an overflowed point', () => {
		const r = new Readout().read([a, blown], 10, 1);
		expect(r.count).toBe(1);
		expect(r.color[0]).toBe('a');
		expect(new Readout().read([blown], 20, 1).count).toBe(0);
		expect(new Readout().read([blown], 0, 1).count).toBe(1);
	});

	it('reads sparse lines inside their span, at their nearest point', () => {
		const r = new Readout().read([sparse], 260, 1);
		expect(r.count).toBe(1);
		expect(r.step[0]).toBe(500);
		expect(new Readout().read([sparse], 900, 5).count).toBe(0);
	});

	it('reads each line at its own x, skipping lines without one', () => {
		const r = new Readout().read([a, b, sparse], (j) => [20, 0, NaN][j], 1);
		expect(r.count).toBe(2);
		expect(Array.from(r.step.subarray(0, 2))).toEqual([20, 0]);
		expect(Array.from(r.value.subarray(0, 2))).toEqual([3, 2]);
	});

	it('marks focused lines', () => {
		const r = new Readout().read([a, b], 10, 1, [b]);
		expect(Array.from(r.focused.subarray(0, 2))).toEqual([0, 1]);
	});

	it('reuses its buffers and grows them for more lines', () => {
		const r = new Readout();
		const buf = r.value;
		r.read([a, b], 10, 1);
		expect(r.value).toBe(buf);
		const many = Array.from({ length: 40 }, (_, i) => line([0, 10], [i, i]));
		r.read(many, 10, 1);
		expect(r.count).toBe(40);
		expect(r.value[r.order[0]]).toBe(39);
		r.read([a], 10, 1);
		expect(r.count).toBe(1);
		expect(r.order).toEqual([0]);
		expect(r.clear().count).toBe(0);
	});
});

describe('tooltipRows', () => {
	const m = { chrome: 30, row: 18, more: 16 };

	it('lists every reading that fits', () => {
		expect(tooltipRows(5, -1, 200, m)).toEqual({
			start: 0,
			rows: 5,
			above: 0,
			below: 0,
			height: 30 + 5 * 18
		});
	});

	it('lists the top ones with a "more below" line when nothing is focused', () => {
		const t = tooltipRows(100, -1, 200, m);
		expect(t.start).toBe(0);
		expect(t.above).toBe(0);
		expect(t.below).toBe(100 - t.rows);
		expect(t.height).toBeLessThanOrEqual(200);
	});

	it('centers the window on the focused reading', () => {
		const t = tooltipRows(100, 50, 200, m);
		expect(t.start).toBeLessThanOrEqual(50);
		expect(t.start + t.rows).toBeGreaterThan(50);
		expect(t.above).toBeGreaterThan(0);
		expect(t.below).toBeGreaterThan(0);
		expect(t.height).toBeLessThanOrEqual(200);
		const last = tooltipRows(100, 99, 200, m);
		expect(last.start + last.rows).toBe(100);
		expect(last.below).toBe(0);
	});

	it('always shows at least one row', () => {
		expect(tooltipRows(10, 3, 20, m).rows).toBe(1);
	});
});

describe('tooltipPosition', () => {
	it('sits right of the crosshair when there is room', () => {
		expect(tooltipPosition(100, 50, 120, 80, 400, 200)).toEqual({ left: 112, top: 38 });
	});

	it('flips left of the crosshair near the right edge', () => {
		expect(tooltipPosition(350, 50, 120, 80, 400, 200).left).toBe(350 - 12 - 120);
	});

	it('stays inside a box too narrow for either side', () => {
		const { left } = tooltipPosition(60, 50, 120, 80, 130, 200);
		expect(left).toBeGreaterThanOrEqual(2);
		expect(left + 120).toBeLessThanOrEqual(128);
	});

	it('keeps inside vertically', () => {
		expect(tooltipPosition(100, 195, 120, 80, 400, 200).top).toBe(200 - 2 - 80);
		expect(tooltipPosition(100, 0, 120, 80, 400, 200).top).toBe(2);
	});
});

describe('placeTip', () => {
	const m = { chrome: 30, row: 18, more: 16 };
	// A grid card's plot, 340×240, hovered at its middle; the window is 1440×900.
	const plot = { x: 170, y: 120, w: 340, h: 240 };
	const window = { x: 800, y: 400, w: 1440, h: 900 };

	it('lists as many rows when floating as inside the plot, only placed in the window', () => {
		const inPlot = placeTip(64, 10, plot, m, 248);
		const floating = placeTip(64, 10, plot, m, 248, window);
		expect(floating.fit).toEqual(inPlot.fit);
		expect(floating.width).toBe(inPlot.width);
		expect(floating.fit.rows).toBeLessThan(64);
		expect(floating.fit.above + floating.fit.below).toBe(64 - floating.fit.rows);
		// Beside the cursor, in window coordinates.
		expect(floating.left).toBe(800 + 16);
		expect(floating.top).toBe(400 - 12);
	});

	it("flips a floating tooltip to the cursor's left at the window's right edge", () => {
		const t = placeTip(5, -1, plot, m, 248, { ...window, x: 1400 });
		expect(t.left).toBe(1400 - 16 - 248);
	});
});
