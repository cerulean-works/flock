import { describe, expect, it } from 'vitest';
import { base64ToBytes } from '#lib/utils.ts';
import { decodeWorkspace, metricSections } from './snapshot.ts';

const QUERY = { page: 1, sort: 'created_at', order: 'desc' as const, size: 100 };

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
	}[];
	columns: { key: string; kind: string }[];
	metrics: string[];
	systemMetrics?: string[];
	numeric: Record<number, number[]>;
	text: Record<number, unknown[]>;
	series: (TestSeries | null)[];
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
	const header = new TextEncoder().encode(
		JSON.stringify({
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
	const size = start + (numericCols.length * R + meta.length + xs.length) * 8 + ys.length * 4;
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
		expect(meta.sections.at(-1)).toEqual({ key: 'system', metrics: [2] });
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

	it('rejects foreign payloads', () => {
		expect(() => decodeWorkspace(new ArrayBuffer(16), QUERY)).toThrow();
	});
});

describe('metric sections', () => {
	it('groups by prefix, unprefixed metrics first', () => {
		const { metrics, sections } = metricSections(['eval/acc', 'loss', 'train/a', 'train/b']);
		expect(sections).toEqual([
			{ key: 'charts', metrics: [1] },
			{ key: 'eval', metrics: [0] },
			{ key: 'train', metrics: [2, 3] }
		]);
		expect(metrics[1].section).toBe('charts');
	});

	it('puts system metrics last, even after a logged system/ prefix', () => {
		const { sections } = metricSections(['system/step_time', 'zeta/x'], ['system.cpu']);
		expect(sections).toEqual([
			{ key: 'zeta', metrics: [1] },
			{ key: 'system', metrics: [0, 2] }
		]);
	});
});
