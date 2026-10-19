import { Series } from '#lib/chart/series.ts';
import type {
	ColumnDef,
	ColumnGroup,
	ColumnGroupSpan,
	ColumnKind,
	MetricDef,
	MetricStream,
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
 *   f32[...]                      y values
 */

const MAGIC = 0x324b4c46; // 'FLK2' little-endian

interface Header {
	entity: string;
	project: string;
	now: number;
	total: number;
	runs_total?: number;
	active: number;
	runs: (Omit<RunInfo, 'systemLines'> & { system_lines: number })[];
	columns: { key: string; kind: ColumnKind }[];
	metrics: string[];
	system_metrics: string[];
	numeric: number[];
	text: Record<string, unknown[]>;
	x_len: number;
	y_len: number;
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
 * Logged metrics are grouped into panels by the prefix before the first `/`, with unprefixed
 * ones under "charts", which comes first. System metrics follow, in a "system" section last.
 */
export function metricSections(
	keys: string[],
	systemKeys: string[] = []
): { metrics: MetricDef[]; sections: SectionDef[] } {
	const def = (key: string, section: string, stream: MetricStream): MetricDef => ({
		key,
		section,
		format: 'num',
		stream
	});
	const metrics = [
		...keys.map((key) => {
			const slash = key.indexOf('/');
			return def(key, slash > 0 ? key.slice(0, slash) : 'charts', 'history');
		}),
		...systemKeys.map((key) => def(key, 'system', 'system'))
	];
	const byKey = new Map<string, SectionDef>();
	metrics.forEach((m, i) => {
		let s = byKey.get(m.section);
		if (!s) byKey.set(m.section, (s = { key: m.section, metrics: [] }));
		s.metrics.push(i);
	});
	const first = byKey.get('charts');
	const last = byKey.get('system');
	const sections = [...byKey.values()].filter((s) => s !== first && s !== last);
	return { metrics, sections: [first, ...sections, last].filter((s) => s != null) };
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

	const columns = header.columns.map((c) => columnDef(c.key, c.kind));
	const { metrics, sections } = metricSections(header.metrics, header.system_metrics);
	return {
		meta: {
			entity: header.entity,
			project: header.project,
			pageSize: query.size,
			now: header.now,
			columns,
			groups: columnGroups(columns),
			metrics,
			sections
		},
		page: {
			query,
			total: header.total,
			// Flocks from before filters serve no runs_total: every run was on the pages.
			projectTotal: header.runs_total ?? header.total,
			active: header.active,
			runs: header.runs.map(({ system_lines, ...run }) => ({ ...run, systemLines: system_lines })),
			metricCount: M,
			num,
			text,
			series
		}
	};
}
