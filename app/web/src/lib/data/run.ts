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
