import { describe, expect, it } from 'vitest';
import { metricSections } from '#lib/data/snapshot.ts';
import type { MetricStream } from '#lib/types.ts';
import { configOf, draftOf, resolvePanel } from './panels.ts';

const { metrics, panels } = metricSections(
	['loss', 'train/acc', 'train/loss'],
	['system.gpu.0.gpu', 'system.gpu.1.gpu', 'system.memory']
);
const find = (stream: MetricStream, key: string) => {
	const i = metrics.findIndex((m) => m.stream === stream && m.key === key);
	return i === -1 ? undefined : i;
};
const panel = (id: string) => panels.find((p) => p.id === id)!;

describe('resolvePanel', () => {
	it('is the panel itself without settings, its title prefix muted', () => {
		const view = resolvePanel(panel('train/acc'), undefined, metrics, find);
		expect(view).toMatchObject({ id: 'train/acc', title: 'train/acc', metrics: [1], prefix: 6 });
		expect(view.config).toEqual({});
		expect(resolvePanel(panel('loss'), undefined, metrics, find).prefix).toBe(0);
	});

	it('keeps system chart titles whole', () => {
		const view = resolvePanel(panel('system:gpu.gpu'), {}, metrics, find);
		expect(view).toMatchObject({ title: 'GPU Utilization (%)', metrics: [3, 4], prefix: 0 });
	});

	it('plots the metrics picked, named after them unless titled', () => {
		const view = resolvePanel(
			panel('train/loss'),
			{ metrics: ['train/loss', 'loss'] },
			metrics,
			find
		);
		expect(view).toMatchObject({ title: 'train/loss, loss', metrics: [2, 0], prefix: 0 });
		const titled = resolvePanel(
			panel('train/loss'),
			{ title: 'Losses', metrics: ['loss'] },
			metrics,
			find
		);
		expect(titled).toMatchObject({ title: 'Losses', metrics: [0], prefix: 0 });
	});

	it('skips metrics of other streams or not on this page; none left plots its own', () => {
		const some = resolvePanel(
			panel('loss'),
			{ metrics: ['gone', 'system.memory', 'train/acc'] },
			metrics,
			find
		);
		expect(some.metrics).toEqual([1]);
		const none = resolvePanel(panel('loss'), { metrics: ['gone'] }, metrics, find);
		expect(none).toMatchObject({ title: 'loss', metrics: [0] });
	});

	it("takes its format from its first metric's", () => {
		const view = resolvePanel(panel('loss'), {}, metrics, find);
		expect(view.format).toBe('num');
		const gpu = resolvePanel(panel('system:gpu.gpu'), {}, metrics, find);
		expect(gpu.format).toBe('pct');
	});
});

describe('panel drafts', () => {
	const defaults = { title: 'loss', metrics: ['loss'] };

	it('round-trip settings', () => {
		const config = {
			title: 'Loss',
			metrics: ['loss', 'train/loss'],
			xMin: 10,
			xLabel: 'Steps',
			yMin: 0,
			yMax: 2.5,
			ignoreOutliers: false,
			smoothing: 0.4,
			showOriginal: true,
			lineWidth: 2,
			legend: true,
			log: true
		};
		const draft = draftOf(config, defaults.metrics);
		expect(draft).toMatchObject({ xMin: '10', xMax: '', ignoreOutliers: 'off', legend: 'on' });
		expect(configOf(draft, defaults)).toEqual({ config, errors: {} });
	});

	it('leaves what equals the defaults unset', () => {
		const draft = draftOf({}, defaults.metrics);
		expect(draft.metrics).toEqual(['loss']);
		expect(configOf(draft, defaults).config).toEqual({});
		expect(configOf({ ...draft, title: ' loss ' }, defaults).config).toEqual({});
	});

	it('reads typed numbers and flags the rest', () => {
		const draft = { ...draftOf({}, defaults.metrics), xMin: ' 1e3 ', yMin: 'abc', yMax: '' };
		expect(configOf(draft, defaults)).toEqual({
			config: { xMin: 1000 },
			errors: { yMin: 'Not a number' }
		});
	});

	it('flags bounds the wrong way round and an empty metric list', () => {
		const draft = { ...draftOf({}, defaults.metrics), yMin: '2', yMax: '1', metrics: [] };
		expect(configOf(draft, defaults)).toEqual({
			config: {},
			errors: { yMax: 'Must be above the minimum', metrics: 'Plot at least one metric' }
		});
	});

	it('keep x bounds with the axis they were set on', () => {
		const draft = { ...draftOf({}, defaults.metrics), xAxis: 'epoch', xMax: '3' };
		const { config } = configOf(draft, defaults, 'epoch');
		expect(config).toEqual({ xAxis: 'epoch', xMax: 3, xRangeAxis: 'epoch' });
		// Opened on another axis, bounds that don't apply there aren't offered.
		expect(draftOf(config, defaults.metrics, 'epoch')).toMatchObject({ xMax: '3' });
		expect(draftOf(config, defaults.metrics, '_step')).toMatchObject({ xMax: '', xAxis: 'epoch' });
		expect(configOf(draftOf({}, defaults.metrics), defaults, '_step').config).toEqual({});
	});

	it('clamps sliders to their range', () => {
		const draft = { ...draftOf({}, defaults.metrics), smoothing: 2, lineWidth: 9 };
		expect(configOf(draft, defaults).config).toEqual({ smoothing: 0.99, lineWidth: 4 });
	});
});
