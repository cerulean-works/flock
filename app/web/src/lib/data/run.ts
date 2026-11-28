import { AxisCurve, noAxes } from '#lib/chart/axes.ts';
import { Series } from '#lib/chart/series.ts';
import type { RunDetail, RunHistory, WorkspaceData } from '#lib/types.ts';
import { columnDef, hiddenMetrics, metricSections } from './snapshot.ts';

/** A stream's curves in plot order, without wandb's bookkeeping (`_step`, `_runtime`, ...). */
const keysOf = (history: RunHistory) =>
	Object.keys(history.series)
		.filter((k) => !k.startsWith('_'))
		.sort();

/** x-axis mappings this page can draw from: wandb's times, and every step metric. */
function runAxes(history: RunHistory, system: RunHistory, steps: Iterable<string>) {
	const axes = noAxes();
	const add = (into: Map<string, AxisCurve>, h: RunHistory, key: string) => {
		const s = h.series[key];
		into.set(key, new AxisCurve(s?.x ?? [], s?.y.map((v) => v ?? NaN) ?? []));
	};
	for (const key of ['_runtime', '_timestamp', ...steps]) add(axes.history, history, key);
	add(axes.system, system, '_timestamp');
	return axes;
}

const toSeries = ({ x, y }: { x: number[]; y: (number | null)[] }) =>
	new Series(
		Float32Array.from(y, (v) => v ?? NaN),
		Float64Array.from(x)
	);

/** The run's attributes its dialogs edit, as the workspace's (text) columns. */
const EDITABLE = [
	['tags', 'json'],
	['group', 'str'],
	['job_type', 'str'],
	['notes', 'str']
] as const;

/**
 * One run as a workspace of one: its panels then work exactly as the project's do (plots,
 * full screen, tooltips), and the live stream keeps them current from the run's line
 * counts. It has no table; its only columns are what the run's dialogs edit.
 */
export function runWorkspace(
	run: RunDetail,
	history: RunHistory,
	system: RunHistory,
	now: number
): WorkspaceData {
	const keys = keysOf(history);
	const systemKeys = keysOf(system);
	const defs = run.metric_defs ?? [];
	const steps = new Set(defs.flatMap((d) => (d.step ? [d.step] : [])));
	const { metrics, panels, sections } = metricSections(
		keys,
		systemKeys,
		hiddenMetrics(keys, [defs])
	);
	return {
		meta: {
			entity: run.entity,
			project: run.project,
			pageSize: 1,
			now,
			columns: EDITABLE.map(([key, kind]) => columnDef(key, kind)),
			groups: [],
			metrics,
			panels,
			sections
		},
		page: {
			query: { page: 1, sort: 'created_at', order: 'desc', size: 1, search: '', filters: [] },
			total: 1,
			projectTotal: 1,
			active: 0,
			runs: [
				{
					id: run.id,
					name: run.display_name || run.id,
					state: run.state,
					ordinal: 0,
					lines: run.history_line_count,
					systemLines: run.system_line_count,
					defs
				}
			],
			metricCount: metrics.length,
			num: EDITABLE.map(() => null),
			text: EDITABLE.map(([key]) => [run[key]]),
			series: [
				...keys.map((k) => toSeries(history.series[k])),
				...systemKeys.map((k) => toSeries(system.series[k]))
			],
			axes: [runAxes(history, system, steps)]
		}
	};
}

/** Nested config or summary values as dotted keys: `optimizer.lr`. Lists stay whole. */
export function flatten(value: Record<string, unknown>, prefix = ''): [string, unknown][] {
	return Object.entries(value).flatMap(([k, v]) =>
		v && typeof v === 'object' && !Array.isArray(v) && !('_type' in v) && Object.keys(v).length
			? flatten(v as Record<string, unknown>, `${prefix}${k}.`)
			: [[`${prefix}${k}`, v] as [string, unknown]]
	);
}

/** One side of a pair: the run and its curves. */
export interface PairSide {
	run: RunDetail;
	history: RunHistory;
	system: RunHistory;
}

/**
 * Two runs, from any projects, as a workspace of two (the compare page's charts): A on row 0, B
 * on row 1, charting only the metrics both logged, so every panel has the two lines. The curves
 * are all here, so nothing is read from either project. `only` lists the metrics one run
 * logged and the other didn't, to name below the charts.
 */
export function pairWorkspace(
	a: PairSide,
	b: PairSide,
	now: number
): { data: WorkspaceData; only: { a: string[]; b: string[] } } {
	const shared = (x: RunHistory, y: RunHistory) => {
		const theirs = new Set(keysOf(y));
		return keysOf(x).filter((k) => theirs.has(k));
	};
	const keys = shared(a.history, b.history);
	const systemKeys = shared(a.system, b.system);
	const without = (x: RunHistory, y: RunHistory) => {
		const theirs = new Set(keysOf(y));
		return keysOf(x).filter((k) => !theirs.has(k));
	};
	const defsA = a.run.metric_defs ?? [];
	const defsB = b.run.metric_defs ?? [];
	const steps = new Set([...defsA, ...defsB].flatMap((d) => (d.step ? [d.step] : [])));
	const { metrics, panels, sections } = metricSections(
		keys,
		systemKeys,
		hiddenMetrics(keys, [defsA, defsB])
	);
	const sides = [a, b];
	return {
		only: { a: without(a.history, b.history), b: without(b.history, a.history) },
		data: {
			meta: {
				// A scope of its own: the plot settings set here (the x axis, smoothing) are kept
				// for every comparison, and neither project's own layout is touched. Nothing is
				// read through it: the curves are all here.
				entity: a.run.entity,
				project: '~compare',
				pageSize: 2,
				now,
				columns: [],
				groups: [],
				metrics,
				panels,
				sections
			},
			page: {
				query: { page: 1, sort: 'created_at', order: 'desc', size: 2, search: '', filters: [] },
				total: 2,
				projectTotal: 2,
				active: 0,
				runs: sides.map((s, i) => ({
					id: s.run.id,
					name: `${i ? 'B' : 'A'} · ${s.run.display_name || s.run.id}`,
					state: s.run.state,
					ordinal: i,
					lines: s.run.history_line_count,
					systemLines: s.run.system_line_count,
					defs: s.run.metric_defs ?? []
				})),
				metricCount: metrics.length,
				num: [],
				text: [],
				series: sides.flatMap((s) => [
					...keys.map((k) => toSeries(s.history.series[k])),
					...systemKeys.map((k) => toSeries(s.system.series[k]))
				]),
				axes: sides.map((s) => runAxes(s.history, s.system, steps))
			}
		}
	};
}
