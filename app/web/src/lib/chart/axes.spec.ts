import { describe, expect, it } from 'vitest';
import {
	AxisCurve,
	definitionOf,
	formatX,
	formatXTick,
	formatXValue,
	mapAt,
	remap,
	remapped,
	resolveAxis,
	sharedStepMetric,
	stepMetricOf,
	unmapAt
} from './axes.ts';
import { Series } from './series.ts';

const series = (x: number[], y: number[]) => new Series(Float32Array.from(y), Float64Array.from(x));

/** Hugging Face's WandbCallback, as Flock reads its definitions. */
const HF = [
	{ key: 'train/global_step' },
	{ glob: '*', step: 'train/global_step' },
	{ key: 'debug', hidden: true }
];

describe('remap', () => {
	it('puts a curve on another axis by interpolating the mapping', () => {
		// train/global_step = 16·(_step + 1), sent as its two ends.
		const map = new AxisCurve([0, 99], [16, 1600]);
		const out = remap(series([0, 1, 10, 99], [4, 3, 2, 1]), map);
		expect(Array.from(out.x)).toEqual([16, 32, 176, 1600]);
		expect(Array.from(out.y)).toEqual([4, 3, 2, 1]);
	});

	it('drops points outside the mapping or where it is not finite', () => {
		const map = new AxisCurve([2, 3, 4], [20, NaN, 40]);
		const out = remap(series([0, 2, 3, 4, 5], [0, 1, 2, 3, 4]), map, 1000);
		expect(Array.from(out.x)).toEqual([1020, 1040]);
		expect(Array.from(out.y)).toEqual([1, 3]);
	});

	it('sorts by x for a metric that goes back and forth, keeping ties in order', () => {
		const map = series([0, 1, 2, 3], [5, 1, 3, 1]); // a logged metric as the x axis
		const out = remap(series([0, 1, 2, 3], [10, 11, 12, 13]), map);
		expect(Array.from(out.x)).toEqual([1, 1, 3, 5]);
		expect(Array.from(out.y)).toEqual([11, 13, 12, 10]);
	});

	it('is memoised until either curve changes', () => {
		const s = series([0, 1], [1, 2]);
		const map = new AxisCurve([0, 1], [0, 10]);
		const a = remapped(s, 'x', map);
		expect(remapped(s, 'x', map)).toBe(a);
		map.append([2], [30]);
		s.append([2], [3]);
		const b = remapped(s, 'x', map);
		expect(b).not.toBe(a);
		expect(Array.from(b.x)).toEqual([0, 10, 30]);
	});
});

describe('mapAt and unmapAt', () => {
	// An eval at the same global step (rows 3 and 4), then a stride change.
	const map = new AxisCurve([0, 3, 4, 6], [16, 64, 64, 128]);

	it('read a mapping and its inverse, interpolated', () => {
		expect(mapAt(map, 1.5)).toBe(40);
		expect(mapAt(map, 5)).toBe(96);
		expect(mapAt(map, 0, -16)).toBe(0);
		expect(unmapAt(map, 40)).toBe(1.5);
		expect(unmapAt(map, 96)).toBe(5);
		expect(unmapAt(map, 0, -16)).toBe(0);
		// A value logged on several rows reads at the first.
		expect(unmapAt(map, 64)).toBe(3);
	});

	it('read nothing past a mapping, but invert to its nearest end', () => {
		expect(mapAt(map, 7)).toBeNaN();
		expect(mapAt(new AxisCurve(), 0)).toBeNaN();
		expect(unmapAt(map, 500)).toBe(6);
		expect(unmapAt(new AxisCurve(), 1)).toBeNaN();
	});
});

describe('AxisCurve', () => {
	it('keeps only the knots of a fixed stride as it grows', () => {
		const c = new AxisCurve();
		expect(c.append([0, 1, 2, 3], [16, 32, 48, 64])).toBe(true);
		expect([c.n, Array.from(c.x), Array.from(c.y)]).toEqual([2, [0, 3], [16, 64]]);
		c.append([4, 5], [64, 80]); // an evaluation at the same global step
		expect(Array.from(c.x)).toEqual([0, 3, 4, 5]);
		expect(c.append([5], [80])).toBe(false); // replayed
	});
});

describe('metric definitions', () => {
	it("prefer a key's own definition, then the longest glob", () => {
		const defs = [...HF, { glob: 'eval/*', step: 'eval/step' }];
		expect(stepMetricOf(defs, 'train/loss')).toBe('train/global_step');
		expect(stepMetricOf(defs, 'eval/acc')).toBe('eval/step');
		expect(stepMetricOf(defs, 'train/global_step')).toBeNull();
		expect(stepMetricOf(defs, 'debug')).toBeNull();
		expect(definitionOf(defs, 'debug')?.hidden).toBe(true);
		expect(stepMetricOf([], 'loss')).toBeNull();
	});

	it('take the first run that defined a metric for a chart of several', () => {
		const explicit = [{ key: 'plain' }, { glob: '*', step: 'gs' }];
		expect(sharedStepMetric([[], HF], 'train/loss')).toBe('train/global_step');
		expect(sharedStepMetric([explicit, HF], 'plain')).toBeNull();
		expect(sharedStepMetric([HF, explicit], 'plain')).toBe('train/global_step');
		expect(sharedStepMetric([[]], 'plain')).toBeNull();
	});

	it('leave the decision to runs that logged the metric, and to a step metric they all have', () => {
		/** Runs by index: which logged the metric, and which logged `train/global_step`. */
		const data = (logged: number[], maps: number[]) => ({
			logged: (i: number) => logged.includes(i),
			maps: (i: number, step: string) => step === 'train/global_step' && maps.includes(i)
		});
		// HF's glob of a run that never logged `memory/peak` names no axis for it.
		expect(sharedStepMetric([HF, []], 'memory/peak', data([1], [0]))).toBeNull();
		expect(sharedStepMetric([HF, []], 'train/loss', data([0], [0]))).toBe('train/global_step');
		// A run that logged it without the step metric would lose its line: `_step` then.
		expect(sharedStepMetric([HF, []], 'train/loss', data([0, 1], [0]))).toBeNull();
		expect(sharedStepMetric([HF, HF], 'train/loss', data([0, 1], [0, 1]))).toBe(
			'train/global_step'
		);
		// Nobody logged it (yet): the definitions alone.
		expect(sharedStepMetric([HF], 'new', data([], [0]))).toBe('train/global_step');
	});
});

describe('resolveAxis', () => {
	it('makes Step the step metric when there is one', () => {
		expect(resolveAxis('_step', 'history', null)).toMatchObject({ id: '_step', map: null });
		expect(resolveAxis('_step', 'history', 'train/global_step')).toMatchObject({
			id: 'train/global_step',
			kind: 'number',
			label: 'train/global_step',
			map: 'train/global_step'
		});
		expect(resolveAxis('epoch', 'history', 'train/global_step').map).toBe('epoch');
	});

	it('maps times through _runtime and _timestamp', () => {
		expect(resolveAxis('_runtime', 'history', null)).toMatchObject({
			kind: 'duration',
			map: '_runtime',
			label: 'Relative Time (Process)'
		});
		expect(resolveAxis('_relative_time', 'history', null)).toMatchObject({
			map: '_timestamp',
			relative: true
		});
		expect(resolveAxis('_timestamp', 'history', null)).toMatchObject({ kind: 'time' });
	});

	it('keeps system metrics on time', () => {
		expect(resolveAxis('_step', 'system', null)).toMatchObject({ id: '_runtime', map: null });
		expect(resolveAxis('epoch', 'system', null).id).toBe('_runtime');
		// Process time is the stream's own x, and shares the crosshair with logged metrics on it.
		expect(resolveAxis('_runtime', 'system', null)).toMatchObject({ id: '_runtime', map: null });
		expect(resolveAxis('_timestamp', 'system', null)).toMatchObject({ map: '_timestamp' });
	});

	it('formats ticks and readouts in its units', () => {
		const step = resolveAxis('_step', 'history', null);
		const metric = resolveAxis('epoch', 'history', null);
		const runtime = resolveAxis('_runtime', 'history', null);
		const wall = resolveAxis('_relative_time', 'history', null);
		expect(formatXTick(step, 12_000, 2000)).toBe('12k');
		expect(formatXTick(metric, 0.5, 0.25)).toBe('0.5');
		expect(formatXTick(runtime, 5400, 1800)).toBe('1h 30m');
		expect(formatX(step, 1200)).toBe('Step 1,200');
		expect(formatX(metric, 1.25)).toBe('epoch 1.25');
		expect(formatX(runtime, 185)).toBe('Runtime 3m 05s');
		expect(formatX(wall, 185)).toBe('Wall time 3m 05s');
		expect(formatX(runtime, 1.274)).toBe('Runtime 1.27s');
		// Tooltip rows, under a header that names the axis.
		expect(formatXValue(step, 1200)).toBe('1,200');
		expect(formatXValue(metric, 1.25)).toBe('1.25');
		expect(formatXValue(runtime, 185)).toBe('3m 05s');
	});
});
