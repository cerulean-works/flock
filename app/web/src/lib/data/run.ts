import { Series } from '#lib/chart/series.ts';
import type { RunDetail, RunHistory, WorkspaceData } from '#lib/types.ts';
import { columnDef, metricSections } from './snapshot.ts';

/** A stream's curves in plot order, without wandb's bookkeeping (`_step`, `_runtime`, ...). */
const keysOf = (history: RunHistory) =>
	Object.keys(history.series)
		.filter((k) => !k.startsWith('_'))
		.sort();

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
 * full screen, the cursor panel), and the live stream keeps them current from the run's line
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
	const { metrics, sections } = metricSections(keys, systemKeys);
	return {
		meta: {
			entity: run.entity,
			project: run.project,
			pageSize: 1,
			now,
			columns: EDITABLE.map(([key, kind]) => columnDef(key, kind)),
			groups: [],
			metrics,
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
					systemLines: run.system_line_count
				}
			],
			metricCount: metrics.length,
			num: EDITABLE.map(() => null),
			text: EDITABLE.map(([key]) => [run[key]]),
			series: [
				...keys.map((k) => toSeries(history.series[k])),
				...systemKeys.map((k) => toSeries(system.series[k]))
			]
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
