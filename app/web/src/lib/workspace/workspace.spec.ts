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

describe('a metric logged where its step metric never was', () => {
	it('stays on _step rather than lose every line', () => {
		// `val/*` is plotted against `train/step`, which the run logged on rows 0-9 only; the
		// final evaluation logged `val/loss` on rows 10-12, past the mapping's end.
		const defs: MetricDefinition[] = [{ key: 'train/step' }, { glob: 'val/*', step: 'train/step' }];
		const ws = workspace([
			{ defs, logged: ['train/step', 'val/loss', 'val/loss_ema'], axes: { 'train/step': 10 } }
		]);
		const m = ws.metricOf('history', 'val/loss')!;
		ws.page.series[slotOf(ws.page.metricCount, 0, m)] = new Series(
			Float32Array.from([3, 2.9, 2.8]),
			Float64Array.from([10, 11, 12])
		);
		expect(plotted(ws, 'val/loss')).toEqual({ axis: '_step', rows: [0] });
		expect(plotted(ws, 'val/loss_ema')).toEqual({ axis: 'train/step', rows: [0] });
	});
});

describe('a cursor followed across x axes', () => {
	// `train/matrix_lr` on its step metric, `train/loss` on `_step`, as in a run's own panels.
	const defs: MetricDefinition[] = [
		{ key: 'train/global_step' },
		{ key: 'train/matrix_lr', step: 'train/global_step' }
	];
	const logged = ['train/global_step', 'train/loss', 'train/matrix_lr'];
	const ws = workspace([
		{ defs, logged, axes: { 'train/global_step': 10 } },
		{ defs, logged, axes: { 'train/global_step': 10 } }
	]);
	// The second run took twice the global steps per logged row: 0, 32, ..., 288.
	ws.page.axes[1].history.set('train/global_step', new AxisCurve([0, 9], [0, 288]));
	const axisOf = (key: string) => ws.axisOf(ws.panels.find((p) => p.id === key)!);
	const lr = axisOf('train/matrix_lr');
	const loss = axisOf('train/loss');

	it('goes through the history row each run logged there, at its own x on the other axis', () => {
		expect([lr.id, loss.id]).toEqual(['train/global_step', '_step']);
		// Hovering the step-metric panel at global step 80: row 4 of the first run, 2.5 of the
		// second; the `_step` panel reads them there.
		const rows = [0, 1].map((row) => ws.historyStepAt(row, lr, 80));
		expect(rows).toEqual([4, 2.5]);
		expect(rows.map((s, row) => ws.xAtHistoryStep(row, loss, s))).toEqual([4, 2.5]);
	});

	it('works the other way: a `_step` panel lights up the step-metric panels', () => {
		const rows = [0, 1].map((row) => ws.historyStepAt(row, loss, 4));
		expect(rows).toEqual([4, 4]);
		expect(rows.map((s, row) => ws.xAtHistoryStep(row, lr, s))).toEqual([80, 128]);
	});

	it('reads nothing for a run without the mapping, or past its ends', () => {
		// A panel set to `train/global_step` by hand, over a run that never logged it.
		const bare = workspace([{ defs, logged: ['train/loss', 'train/matrix_lr'] }]);
		expect(bare.historyStepAt(0, lr, 80)).toBeNaN();
		expect(bare.xAtHistoryStep(0, lr, 4)).toBeNaN();
		expect(ws.xAtHistoryStep(0, lr, 12)).toBeNaN();
	});
});
