import { AxisCurve, definitionOf, noAxes, type RunAxes } from '#lib/chart/axes.ts';
import { Series } from '#lib/chart/series.ts';
import { systemCharts } from './system-metrics.ts';
import type {
	ColumnDef,
	ColumnGroup,
	ColumnGroupSpan,
	ColumnKind,
	MetricDef,
	MetricDefinition,
	PanelDef,
	RunInfo,
	RunQuery,
	SectionDef,
	WorkspaceData
} from '#lib/types.ts';

/**
 * Decoder for Flock's workspace snapshot (`app/api/src/api/dashboard.py`, format `FLK2`):
 * a page of the runs table with every column, plus every metric's curve for every run on it:
 * logged metrics against `_step`, then system metrics against `_runtime`.
 * Numeric cells and curves become typed-array views into the response buffer (no copies).
 *
 *   b"FLK2"  u32 header length  header JSON  (zero-padded to 8 bytes)
 *   f64[numeric columns × runs]   numeric cells, column-major (NaN = missing)
 *   f64[runs × metrics × 6]       per series (logged, then system metrics): y offset, n, raw count, x0, dx, x offset (-1 = regular)
 *   f64[...]                      x values of irregular series
 *   f32[...]                      y values (zero-padded to 8 bytes)
 *   f64[runs × axes × 2]          per x-axis mapping (`axes` against `_step`, then
 *                                 `system_axes` against `_runtime`): offset, n
 *   f64[...]                      mapping knots: n x values, then n values
 *
 * The mappings came later: a header without `axes` has none, and every axis but `_step`
 * (`_runtime` for system metrics) is then empty.
 */

const MAGIC = 0x324b4c46; // 'FLK2' little-endian

interface Header {
	entity: string;
	project: string;
	now: number;
	total: number;
	runs_total?: number;
	active: number;
	runs: (Omit<RunInfo, 'systemLines' | 'defs'> & {
		system_lines: number;
		metric_defs?: MetricDefinition[];
	})[];
	columns: { key: string; kind: ColumnKind }[];
	metrics: string[];
	system_metrics: string[];
	numeric: number[];
	text: Record<string, unknown[]>;
	x_len: number;
	y_len: number;
	axes?: string[];
	system_axes?: string[];
	axes_len?: number;
}

const INFO_LABELS: Record<string, string> = {
	name: 'id',
	created_at: 'created',
	updated_at: 'updated',
	exitcode: 'exit code'
};

const WIDTHS: Record<ColumnKind, number> = {
	num: 124,
	time: 148,
	duration: 104,
	str: 160,
	state: 116,
	bool: 96,
	json: 160
};

export function columnDef(key: string, kind: ColumnKind): ColumnDef {
	let group: ColumnGroup = 'info';
	let label = INFO_LABELS[key] ?? key;
	if (key.startsWith('config.')) {
		group = 'config';
		label = key.slice(7);
	} else if (key.startsWith('summary.')) {
		group = 'summary';
		label = key.slice(8);
	}
	const width = key === 'name' ? 104 : WIDTHS[kind];
	return { key, label, group, kind, width };
}

const GROUP_LABELS: Record<ColumnGroup, string> = {
	info: 'Run',
	config: 'Config',
	summary: 'Summary'
};

/** Contiguous column groups, in schema order. */
export function columnGroups(columns: ColumnDef[]): ColumnGroupSpan[] {
	const out: ColumnGroupSpan[] = [];
	columns.forEach((c, i) => {
		const last = out[out.length - 1];
		if (last?.key === c.group) last.end = i + 1;
		else out.push({ key: c.group, label: GROUP_LABELS[c.group], start: i, end: i + 1 });
	});
	return out;
}

/**
 * Charts for a page's metrics. Logged metrics are a panel each, grouped into sections by the
 * prefix before the first `/`, with unprefixed ones under "charts", which comes first. System
 * metrics are grouped into W&B's charts (one line per GPU, core, disk...), in a "system"
 * section last; any no chart claims get a panel each.
 *
 * `metrics` stays in stream order (logged, then system), as series slots are laid out.
 * Logged metrics that `hidden` names (`define_metric(hidden=True)`) get no panel of their own,
 * as on W&B; they can still be plotted, or used as an x axis.
 */
export function metricSections(
	keys: string[],
	systemKeys: string[] = [],
	hidden: ReadonlySet<string> = new Set()
): { metrics: MetricDef[]; panels: PanelDef[]; sections: SectionDef[] } {
	const metrics: MetricDef[] = [
		...keys.map((key): MetricDef => {
			const slash = key.indexOf('/');
			const section = slash > 0 ? key.slice(0, slash) : 'charts';
			return { key, section, format: 'num', stream: 'history', label: key };
		}),
		...systemKeys.map((key): MetricDef => ({
			key,
			section: 'system',
			format: 'num',
			stream: 'system',
			label: key
		}))
	];
	const panels: PanelDef[] = keys.flatMap((key, i): PanelDef[] =>
		hidden.has(key)
			? []
			: [
					{
						id: key,
						title: key,
						section: metrics[i].section,
						stream: 'history',
						format: 'num',
						metrics: [i]
					}
				]
	);

	const system = new Map(systemKeys.map((key, i) => [key, keys.length + i]));
	const charted = new Set<number>();
	for (const chart of systemCharts(systemKeys)) {
		const of: number[] = [];
		for (const { key, label } of chart.series) {
			const m = system.get(key);
			if (m === undefined || charted.has(m)) continue;
			charted.add(m);
			metrics[m] = { ...metrics[m], label, format: chart.format };
			of.push(m);
		}
		if (!of.length) continue;
		panels.push({
			id: `system:${chart.id}`,
			title: chart.title,
			section: 'system',
			stream: 'system',
			format: chart.format,
			metrics: of
		});
	}
	for (const m of system.values()) {
		if (charted.has(m)) continue;
		const { key } = metrics[m];
		panels.push({
			id: `system:${key}`,
			title: key,
			section: 'system',
			stream: 'system',
			format: 'num',
			metrics: [m]
		});
	}

	const byKey = new Map<string, SectionDef>();
	panels.forEach((p, i) => {
		let s = byKey.get(p.section);
		if (!s) byKey.set(p.section, (s = { key: p.section, panels: [] }));
		s.panels.push(i);
	});
	const first = byKey.get('charts');
	const last = byKey.get('system');
	const sections = [...byKey.values()].filter((s) => s !== first && s !== last);
	return { metrics, panels, sections: [first, ...sections, last].filter((s) => s != null) };
}

/** Logged metrics some run hides (`define_metric(hidden=True)`, or a hidden glob). */
export function hiddenMetrics(keys: readonly string[], defs: readonly MetricDefinition[][]) {
	return new Set(keys.filter((k) => defs.some((d) => definitionOf(d, k)?.hidden)));
}

export function decodeWorkspace(buf: ArrayBuffer, query: RunQuery): WorkspaceData {
	const view = new DataView(buf);
	if (buf.byteLength < 8 || view.getUint32(0, true) !== MAGIC) {
		throw new Error('Malformed workspace snapshot');
	}
	const jsonLen = view.getUint32(4, true);
	const header: Header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 8, jsonLen)));
	const R = header.runs.length;
	const M = header.metrics.length + header.system_metrics.length;
	const C = header.columns.length;

	let pos = (8 + jsonLen + 7) & ~7;
	const numBlock = new Float64Array(buf, pos, header.numeric.length * R);
	pos += numBlock.byteLength;
	const seriesMeta = new Float64Array(buf, pos, R * M * 6);
	pos += seriesMeta.byteLength;
	const xs = new Float64Array(buf, pos, header.x_len);
	pos += xs.byteLength;
	const ys = new Float32Array(buf, pos, header.y_len);
	pos = (pos + ys.byteLength + 7) & ~7;
	const axisKeys = [...(header.axes ?? []), ...(header.system_axes ?? [])];
	const historyAxes = header.axes?.length ?? 0;
	const axisMeta =
		axisKeys.length && header.axes_len != null
			? new Float64Array(buf, pos, R * axisKeys.length * 2)
			: null;
	const axisValues = axisMeta
		? new Float64Array(buf, pos + axisMeta.byteLength, header.axes_len!)
		: null;

	const num: (Float64Array | null)[] = new Array(C).fill(null);
	header.numeric.forEach((c, i) => (num[c] = numBlock.subarray(i * R, (i + 1) * R)));
	const text: (unknown[] | null)[] = new Array(C).fill(null);
	for (const [c, values] of Object.entries(header.text)) text[+c] = values;

	const series: (Series | null)[] = new Array(R * M).fill(null);
	for (let s = 0; s < R * M; s++) {
		const o = s * 6;
		const n = seriesMeta[o + 1];
		if (!n) continue;
		const y = ys.subarray(seriesMeta[o], seriesMeta[o] + n);
		const xOff = seriesMeta[o + 5];
		series[s] =
			xOff >= 0
				? new Series(y, xs.subarray(xOff, xOff + n))
				: new Series(y, null, {
						raw: seriesMeta[o + 2],
						x0: seriesMeta[o + 3],
						dx: seriesMeta[o + 4]
					});
	}

	// Copied: live runs grow them.
	const axes: RunAxes[] = header.runs.map((_, r) => {
		const out = noAxes();
		axisKeys.forEach((key, a) => {
			const o = (r * axisKeys.length + a) * 2;
			const [off, n] = axisMeta ? [axisMeta[o], axisMeta[o + 1]] : [0, 0];
			const x = axisValues?.subarray(off, off + n) ?? [];
			const y = axisValues?.subarray(off + n, off + 2 * n) ?? [];
			out[a < historyAxes ? 'history' : 'system'].set(key, new AxisCurve(x, y));
		});
		return out;
	});
	const defs = header.runs.map((r) => r.metric_defs ?? []);

	const columns = header.columns.map((c) => columnDef(c.key, c.kind));
	// No panel for a metric none of the page's runs has points of (a key the server lists, but
	// whose every curve came back empty): it would only say "No runs plotted".
	const hidden = hiddenMetrics(header.metrics, defs);
	header.metrics.forEach((key, m) => {
		for (let r = 0; r < R; r++) if (series[r * M + m]?.n) return;
		hidden.add(key);
	});
	const { metrics, panels, sections } = metricSections(
		header.metrics,
		header.system_metrics,
		hidden
	);
	return {
		meta: {
			entity: header.entity,
			project: header.project,
			pageSize: query.size,
			now: header.now,
			columns,
			groups: columnGroups(columns),
			metrics,
			panels,
			sections
		},
		page: {
			query,
			total: header.total,
			// Flocks from before filters serve no runs_total: every run was on the pages.
			projectTotal: header.runs_total ?? header.total,
			active: header.active,
			runs: header.runs.map(({ system_lines, metric_defs = [], ...run }) => ({
				...run,
				systemLines: system_lines,
				defs: metric_defs
			})),
			metricCount: M,
			num,
			text,
			series,
			axes
		}
	};
}
