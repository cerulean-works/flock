import { describe, expect, it } from 'vitest';
import { base64ToBytes } from '#lib/utils.ts';
import { decodeWorkspace, metricSections } from './snapshot.ts';

const QUERY = {
	page: 1,
	sort: 'created_at',
	order: 'desc' as const,
	size: 100,
	search: '',
	filters: []
};

interface TestSeries {
	y: number[];
	/** Explicit steps; omitted for regular series. */
	x?: number[];
	regular?: { raw: number; x0: number; dx: number };
}

/** Builds an FLK2 snapshot exactly as app/api/src/api/dashboard.py lays it out. */
function encode(opts: {
	runs: {
		id: string;
		name: string;
		state: string;
		ordinal: number;
		lines: number;
		system_lines: number;
		metric_defs?: unknown[];
	}[];
	columns: { key: string; kind: string }[];
	metrics: string[];
	systemMetrics?: string[];
	numeric: Record<number, number[]>;
	text: Record<number, unknown[]>;
	series: (TestSeries | null)[];
	/** x-axis mappings, from servers that send them: per run and axis, knots (x, y) or null. */
	axes?: { keys: string[]; system: string[]; curves: ([number[], number[]] | null)[] };
}): ArrayBuffer {
	const numericCols = Object.keys(opts.numeric).map(Number);
	const meta: number[] = [];
	const xs: number[] = [];
	const ys: number[] = [];
	for (const s of opts.series) {
		if (!s) {
			meta.push(0, 0, 0, 0, 0, -1);
			continue;
		}
		const r = s.regular;
		meta.push(
			ys.length,
			s.y.length,
			r?.raw ?? s.y.length,
			r?.x0 ?? 0,
			r?.dx ?? 0,
			r ? -1 : xs.length
		);
		if (!r) xs.push(...s.x!);
		ys.push(...s.y);
	}
	const axisMeta: number[] = [];
	const axisValues: number[] = [];
	for (const c of opts.axes?.curves ?? []) {
		axisMeta.push(axisValues.length, c ? c[0].length : 0);
		if (c) axisValues.push(...c[0], ...c[1]);
	}
	const header = new TextEncoder().encode(
		JSON.stringify({
			...(opts.axes && {
				axes: opts.axes.keys,
				system_axes: opts.axes.system,
				axes_len: axisValues.length
			}),
			entity: 'flock',
			project: 'p',
			now: 1_800_000_000,
			total: 7,
			active: 1,
			offset: 0,
			runs: opts.runs,
			columns: opts.columns,
			metrics: opts.metrics,
			system_metrics: opts.systemMetrics ?? [],
			numeric: numericCols,
			text: opts.text,
			points: 500,
			x_len: xs.length,
			y_len: ys.length
		})
	);
	const start = (8 + header.length + 7) & ~7;
	const R = opts.runs.length;
	const yEnd = start + (numericCols.length * R + meta.length + xs.length) * 8 + ys.length * 4;
	const axesAt = (yEnd + 7) & ~7;
	const size = opts.axes ? axesAt + (axisMeta.length + axisValues.length) * 8 : yEnd;
	const buf = new ArrayBuffer(size);
	const view = new DataView(buf);
	view.setUint32(0, 0x324b4c46, true);
	view.setUint32(4, header.length, true);
	new Uint8Array(buf, 8).set(header);
	let pos = start;
	for (const c of numericCols) {
		new Float64Array(buf, pos, R).set(opts.numeric[c]);
		pos += R * 8;
	}
	new Float64Array(buf, pos, meta.length).set(meta);
	pos += meta.length * 8;
	new Float64Array(buf, pos, xs.length).set(xs);
	pos += xs.length * 8;
	new Float32Array(buf, pos, ys.length).set(ys);
	if (opts.axes) {
		new Float64Array(buf, axesAt, axisMeta.length).set(axisMeta);
		new Float64Array(buf, axesAt + axisMeta.length * 8, axisValues.length).set(axisValues);
	}
	return buf;
}

const sample = () =>
	encode({
		runs: [
			{ id: 'a', name: 'run-a', state: 'running', ordinal: 3, lines: 40, system_lines: 6 },
			{ id: 'b', name: 'run-b', state: 'finished', ordinal: 1, lines: 10, system_lines: 0 }
		],
		columns: [
			{ key: 'state', kind: 'state' },
			{ key: 'created_at', kind: 'time' },
			{ key: 'config.lr', kind: 'num' },
			{ key: 'summary.loss', kind: 'num' }
		],
		metrics: ['loss', 'train/acc'],
		systemMetrics: ['system.cpu'],
		numeric: { 1: [1_799_999_000, 1_799_990_000], 2: [0.1, NaN], 3: [0.5, 0.25] },
		text: { 0: ['running', 'finished'] },
		// per run: loss, train/acc, system.cpu (against runtime seconds)
		series: [
			{ y: [3, 2, 1], regular: { raw: 5, x0: 0, dx: 10 } },
			{ y: [0.5, 0.75], x: [3, 17] },
			{ y: [12, 80], x: [2.5, 17.5] },
			null,
			{ y: [9], x: [0] },
			null
		]
	});

describe('workspace snapshot decoding', () => {
	it('decodes runs, column definitions and cells', () => {
		const { meta, page } = decodeWorkspace(sample(), QUERY);
		expect(page.runs.map((r) => [r.id, r.state, r.lines, r.systemLines])).toEqual([
			['a', 'running', 40, 6],
			['b', 'finished', 10, 0]
		]);
		expect(page.total).toBe(7);
		expect(page.active).toBe(1);
		expect(meta.now).toBe(1_800_000_000);
		expect(meta.columns.map((c) => [c.key, c.group, c.label, c.kind])).toEqual([
			['state', 'info', 'state', 'state'],
			['created_at', 'info', 'created', 'time'],
			['config.lr', 'config', 'lr', 'num'],
			['summary.loss', 'summary', 'loss', 'num']
		]);
		expect(Array.from(page.num[2]!)).toEqual([0.1, NaN]);
		expect(page.num[0]).toBeNull();
		expect(page.text[0]).toEqual(['running', 'finished']);
	});

	it('reconstructs regular steps and keeps explicit ones', () => {
		const { page } = decodeWorkspace(sample(), QUERY);
		const regular = page.series[0]!;
		// raw indices for 3 of 5 samples: 0, 2, 4 → steps 0, 20, 40
		expect(Array.from(regular.x)).toEqual([0, 20, 40]);
		expect(Array.from(regular.y)).toEqual([3, 2, 1]);
		expect(Array.from(page.series[1]!.x)).toEqual([3, 17]);
		expect(page.series[3]).toBeNull();
		expect(page.series[4]!.n).toBe(1);
	});

	it('appends system metrics after logged ones, in their own section', () => {
		const { meta, page } = decodeWorkspace(sample(), QUERY);
		expect(page.metricCount).toBe(3);
		expect(meta.metrics.map((m) => [m.key, m.stream])).toEqual([
			['loss', 'history'],
			['train/acc', 'history'],
			['system.cpu', 'system']
		]);
		expect(meta.panels.map((p) => [p.id, p.metrics])).toEqual([
			['loss', [0]],
			['train/acc', [1]],
			['system:cpu', [2]]
		]);
		expect(meta.sections.at(-1)).toEqual({ key: 'system', panels: [2] });
		expect(Array.from(page.series[2]!.x)).toEqual([2.5, 17.5]);
		expect(Array.from(page.series[2]!.y)).toEqual([12, 80]);
		expect(page.series[5]).toBeNull();
	});

	it('survives the base64 path used for SSR inlining', () => {
		const bytes = new Uint8Array(sample());
		const b64 = btoa(String.fromCharCode(...bytes));
		const { page } = decodeWorkspace(base64ToBytes(b64).buffer, QUERY);
		expect(Array.from(page.series[0]!.y)).toEqual([3, 2, 1]);
	});

	it('reads x-axis mappings, after the padding that ends the y values', () => {
		const buf = encode({
			runs: [
				{ id: 'a', name: 'a', state: 'running', ordinal: 0, lines: 3, system_lines: 2 },
				{ id: 'b', name: 'b', state: 'finished', ordinal: 1, lines: 3, system_lines: 0 }
			],
			columns: [],
			metrics: ['loss'],
			numeric: {},
			text: {},
			// An odd number of y values: the mappings start after padding.
			series: [{ y: [3, 2, 1], x: [0, 1, 2] }, null],
			axes: {
				keys: ['_runtime', '_timestamp', 'train/step'],
				system: ['_timestamp'],
				curves: [
					[
						[0, 2],
						[0.5, 1.5]
					],
					[
						[0, 2],
						[1.79e9 + 0.25, 1.79e9 + 2]
					],
					[
						[0, 2],
						[16, 48]
					],
					[
						[0.5, 3.5],
						[1.79e9 + 1, 1.79e9 + 4]
					],
					[
						[0, 1],
						[7, 8]
					],
					null,
					null,
					null
				]
			}
		});
		const { page } = decodeWorkspace(buf, QUERY);
		const a = page.axes[0];
		expect([...a.history.keys()]).toEqual(['_runtime', '_timestamp', 'train/step']);
		expect(Array.from(a.history.get('_timestamp')!.y)).toEqual([1.79e9 + 0.25, 1.79e9 + 2]);
		expect(Array.from(a.history.get('train/step')!.x)).toEqual([0, 2]);
		expect(Array.from(a.system.get('_timestamp')!.x)).toEqual([0.5, 3.5]);
		expect(page.axes[1].history.get('train/step')!.n).toBe(0);
		expect(page.runs[0].defs).toEqual([]);
	});

	it('carries metric definitions, and leaves hidden metrics without a panel', () => {
		const buf = encode({
			runs: [
				{
					id: 'a',
					name: 'a',
					state: 'running',
					ordinal: 0,
					lines: 1,
					system_lines: 0,
					metric_defs: [
						{ glob: '*', step: 'gs' },
						{ key: 'debug', hidden: true }
					]
				}
			],
			columns: [],
			metrics: ['debug', 'gs', 'loss'],
			numeric: {},
			text: {},
			series: [null, null, null]
		});
		const { meta, page } = decodeWorkspace(buf, QUERY);
		expect(page.runs[0].defs[0]).toEqual({ glob: '*', step: 'gs' });
		expect(meta.metrics.map((m) => m.key)).toEqual(['debug', 'gs', 'loss']);
		expect(meta.panels.map((p) => p.id)).toEqual(['gs', 'loss']);
		// Servers from before mappings send none: every run still gets (empty) axes.
		expect(page.axes[0].history.size).toBe(0);
	});

	it('rejects foreign payloads', () => {
		expect(() => decodeWorkspace(new ArrayBuffer(16), QUERY)).toThrow();
	});
});

describe('metric sections', () => {
	it('groups by prefix, unprefixed metrics first', () => {
		const { metrics, panels, sections } = metricSections([
			'eval/acc',
			'loss',
			'train/a',
			'train/b'
		]);
		expect(sections).toEqual([
			{ key: 'charts', panels: [1] },
			{ key: 'eval', panels: [0] },
			{ key: 'train', panels: [2, 3] }
		]);
		expect(metrics[1].section).toBe('charts');
		// A logged metric is a panel of its own, named and identified by its key.
		expect(panels[2]).toEqual({
			id: 'train/a',
			title: 'train/a',
			section: 'train',
			stream: 'history',
			format: 'num',
			metrics: [2]
		});
		expect(metrics[2].label).toBe('train/a');
	});

	it('puts system metrics last, even after a logged system/ prefix', () => {
		const { sections } = metricSections(['system/step_time', 'zeta/x'], ['system.cpu']);
		expect(sections).toEqual([
			{ key: 'zeta', panels: [1] },
			{ key: 'system', panels: [0, 2] }
		]);
	});

	it('groups system metrics into one chart per measure, a line per device', () => {
		const { metrics, panels, sections } = metricSections(
			['loss'],
			['system.gpu.1.gpu', 'system.gpu.0.gpu', 'system.gpu.0.powerWatts', 'system.memory']
		);
		// Metrics keep stream order, as series slots are laid out.
		expect(metrics.map((m) => m.key)).toEqual([
			'loss',
			'system.gpu.1.gpu',
			'system.gpu.0.gpu',
			'system.gpu.0.powerWatts',
			'system.memory'
		]);
		expect(panels.slice(1).map((p) => [p.id, p.title, p.metrics])).toEqual([
			['system:gpu.gpu', 'GPU Utilization (%)', [2, 1]],
			['system:gpu.powerWatts', 'GPU Power Usage (W)', [3]],
			['system:memory_percent', 'System Memory Utilization (%)', [4]]
		]);
		expect(panels[1]).toMatchObject({ section: 'system', stream: 'system', format: 'pct' });
		expect(metrics[2]).toMatchObject({ label: 'GPU 0 Utilization (%)', format: 'pct' });
		expect(sections).toEqual([
			{ key: 'charts', panels: [0] },
			{ key: 'system', panels: [1, 2, 3] }
		]);
	});
});
