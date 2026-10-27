import { describe, expect, it } from 'vitest';
import { AxisCurve, noAxes } from '#lib/chart/axes.ts';
import { Series, slotOf } from '#lib/chart/series.ts';
import { metricSections } from '#lib/data/snapshot.ts';
import type { MetricDefinition, ProjectMeta, RunInfo, RunPage } from '#lib/types.ts';
import { Workspace } from './workspace.svelte.ts';

const steps = (n: number) => Float64Array.from({ length: n }, (_, i) => i);
const line = (n: number) => new Series(new Float32Array(n).fill(1), steps(n));

/**
 * A page of runs as the snapshot decodes it: per run its definitions, the metrics it logged
 * (10 points each, against `_step`) and its step-metric mappings.
 */
function workspace(
	runs: { defs: MetricDefinition[]; logged: string[]; axes?: Record<string, number> }[]
) {
	const keys = [...new Set(runs.flatMap((r) => r.logged))].sort();
	const { metrics, panels, sections } = metricSections(keys);
	const meta: ProjectMeta = {
		entity: 'flock',
		project: 'midterm-nanogpt',
		pageSize: 100,
		now: 0,
		columns: [],
		groups: [],
		metrics,
		panels,
		sections
	};
	const M = metrics.length;
	const series: (Series | null)[] = new Array(runs.length * M).fill(null);
	runs.forEach((r, row) => {
		for (const key of r.logged) series[slotOf(M, row, keys.indexOf(key))] = line(10);
	});
	const page: RunPage = {
		query: { page: 1, sort: 'created_at', order: 'desc', size: 100, search: '', filters: [] },
		total: runs.length,
		projectTotal: runs.length,
		active: 0,
		runs: runs.map((r, i): RunInfo => ({
			id: `r${i}`,
			name: `r${i}`,
			state: 'finished',
			ordinal: i,
			lines: 10,
			systemLines: 0,
			defs: r.defs
		})),
		metricCount: M,
		num: [],
		text: [],
		series,
		axes: runs.map((r) => {
			const out = noAxes();
			// A step metric logged at 16 per `_step`, kept to its two ends.
			for (const [key, n] of Object.entries(r.axes ?? {}))
				out.history.set(key, new AxisCurve([0, n - 1], [16, 16 * n]));
			return out;
		})
	};
	return new Workspace(
		() => meta,
		() => page
	);
}

/** Page rows with a line in the panel of `key`, on the axis it plots on. */
function plotted(ws: Workspace, key: string) {
	const panel = ws.panels.findIndex((p) => p.id === key);
	const view = ws.panels[panel];
	const axis = ws.axisOf(view);
	return {
		axis: axis.id,
		rows: ws.visible.filter((row) => ws.lineSeries(row, view.metrics[0], axis)?.n)
	};
}

const HF: MetricDefinition[] = [
	{ key: 'train/global_step' },
	{ glob: '*', step: 'train/global_step' }
];

describe('panels of a page whose runs define different step metrics', () => {
	// The newest run (first on the page) is a Hugging Face run: its `*` glob names a step
	// metric for every key, including ones only the page's other runs logged.
	const ws = workspace([
		{
			defs: HF,
			logged: ['train/global_step', 'train/loss'],
			axes: { 'train/global_step': 10 }
		},
		{ defs: [], logged: ['accepted_alpha', 'accounted_step', 'memory/gpu0_peak_allocated_bytes'] },
		{ defs: [], logged: ['accepted_alpha', 'accounted_step', 'memory/gpu0_peak_allocated_bytes'] }
	]);

	it("plots a metric on the step metric of the runs that logged it, not another run's glob", () => {
		for (const key of ['accepted_alpha', 'accounted_step', 'memory/gpu0_peak_allocated_bytes'])
			expect(plotted(ws, key)).toEqual({ axis: '_step', rows: [1, 2] });
		expect(plotted(ws, 'train/loss')).toEqual({ axis: 'train/global_step', rows: [0] });
	});

	it('stays on _step when a run that logged the metric has no step metric to map it to', () => {
		const mixed = workspace([
			{ defs: HF, logged: ['train/global_step', 'loss'], axes: { 'train/global_step': 10 } },
			{ defs: [], logged: ['loss'] }
		]);
		expect(plotted(mixed, 'loss')).toEqual({ axis: '_step', rows: [0, 1] });
	});
});
