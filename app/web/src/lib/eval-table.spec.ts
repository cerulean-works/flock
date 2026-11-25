import { describe, expect, it } from 'vitest';
import { evalTable, type EvalTableRun } from './eval-table.ts';

const BANK = {
	dtype: 'bfloat16',
	attention: 'sdpa',
	sdpa_backend: 'EFFICIENT_ATTENTION',
	batch_tokens: 16384,
	max_length: 4096,
	add_bos: false
};

/**
 * A run as `flock eval log` writes it, flattened into columns: tasks `t1` and `t2` (accuracies
 * and bits per byte) and `c1` (bits per byte only), the suites `qa` (their accuracy), `qa:bpb`
 * (their bits per byte), `code` (of `c1`) and `zh` (of `qa`), and `base`, which only groups
 * `qa` and `code` and has no score. A suite's hash is `h-<suite>` unless `hashes` says otherwise.
 */
function run(
	row: number,
	acc: [number, number],
	bpb: [number, number],
	code: number,
	{ settings = {}, hashes = {} }: { settings?: object; hashes?: Record<string, string> } = {}
): EvalTableRun {
	const qa = (acc[0] + acc[1]) / 2;
	const scores = { qa, 'qa:bpb': (bpb[0] + bpb[1]) / 2, code, zh: qa };
	const members = { qa: ['t1', 't2'], 'qa:bpb': ['t1', 't2'], code: ['c1'], zh: ['qa'] };
	const values: Record<string, unknown> = {
		'summary.t1/acc_per_char': acc[0],
		'summary.t1/bpb': bpb[0],
		'summary.t2/acc_per_char': acc[1],
		'summary.t2/bpb': bpb[1],
		'summary.c1/bpb': code,
		'config.suite_members.base': ['qa', 'code']
	};
	for (const [suite, score] of Object.entries(scores)) {
		values[`summary.suites/${suite}`] = score;
		values[`config.suite_hashes.${suite}`] = hashes[suite] ?? `h-${suite}`;
		values[`config.suite_members.${suite}`] = members[suite as keyof typeof members];
	}
	for (const [key, value] of Object.entries({ ...BANK, ...settings }))
		values[`config.${key}`] = value;
	return { row, ordinal: row, values };
}

describe('the Evals table', () => {
	it('shows the suites no other suite holds, accuracy first, each scale read from its members', () => {
		const table = evalTable([run(0, [0.5, 0.7], [1.2, 0.8], 0.6)]);

		// `qa` sits inside `zh`; `base` has no score, so what it holds is on top.
		expect(table.top).toEqual(['zh', 'code', 'qa:bpb']);
		expect(table.columns.zh).toEqual({ suite: 'zh', lower: false, hash: 'h-zh', children: ['qa'] });
		expect(table.columns.qa.lower).toBe(false);
		// `code` is bits per byte though its name doesn't say so: its score is its task's bpb.
		expect(table.columns.code.lower).toBe(true);
		expect(table.columns['qa:bpb'].lower).toBe(true);
	});

	it('ranks each column in its own direction, ties sharing a rank, and tints best to worst', () => {
		const table = evalTable([
			run(0, [0.5, 0.5], [1.2, 0.8], 0.6),
			run(1, [0.75, 0.75], [0.9, 0.7], 0.6),
			run(2, [0.25, 0.25], [1.5, 1.5], 0.9)
		]);
		const ranks = (suite: string) => table.rows.map((r) => r.cells[suite].rank);

		expect(ranks('zh')).toEqual([2, 1, 3]);
		expect(ranks('qa:bpb')).toEqual([2, 1, 3]); // lower is better
		expect(ranks('code')).toEqual([1, 1, 3]);
		expect(table.rows.map((r) => r.cells.zh.heat)).toEqual([0.5, 1, 0]);

		// Each mean rank over the top suites of one scale only.
		expect(table.rows.map((r) => r.accuracyRank)).toEqual([2, 1, 3]);
		expect(table.rows.map((r) => r.bpbRank)).toEqual([1.5, 1, 3]);
	});

	it('leaves a run scored under other settings out of every rank, naming what differs', () => {
		const table = evalTable([
			run(0, [0.5, 0.5], [1, 1], 1),
			run(1, [0.9, 0.9], [0.5, 0.5], 0.5, { settings: { dtype: 'float32', max_length: 2048 } }),
			run(2, [0.4, 0.4], [1.1, 1.1], 1.1)
		]);

		expect(table.rows.map((r) => r.differs)).toEqual([[], ['dtype', 'max_length'], []]);
		expect(table.rows[1].cells.zh).toEqual({ value: 0.9, otherHash: null, rank: null, heat: null });
		expect(table.rows[1].accuracyRank).toBeNull();
		// The best of the others is first: the FP32 run isn't counted.
		expect(table.rows.map((r) => r.cells.zh.rank)).toEqual([1, null, 2]);
	});

	it('labels a run on another GPU and an instruction-tuned model', () => {
		const a = run(0, [0.5, 0.5], [1, 1], 1);
		const b = run(1, [0.5, 0.5], [1, 1], 1);
		const c = run(2, [0.5, 0.5], [1, 1], 1);
		Object.assign(a.values, { 'config.device': 'NVIDIA H200', 'config.model': 'meta/Llama@1' });
		Object.assign(b.values, {
			'config.device': 'NVIDIA H200',
			'config.model': 'x/Breeze-Instruct@2'
		});
		Object.assign(c.values, { 'config.device': 'NVIDIA H100 80GB HBM3' });
		const table = evalTable([a, b, c]);

		expect(table.rows.map((r) => [r.device, r.instruct])).toEqual([
			[null, false],
			[null, true],
			['NVIDIA H100 80GB HBM3', false]
		]);
		// A label, not another instrument: the H100 run is ranked with the rest.
		expect(table.rows[2].cells.zh.rank).toBe(1);
	});

	it('takes the settings of the oldest run when two sets are as common', () => {
		const table = evalTable([
			run(5, [0.5, 0.5], [1, 1], 1, { settings: { dtype: 'float32' } }),
			run(3, [0.4, 0.4], [1, 1], 1)
		]);

		expect(table.rows.map((r) => r.differs)).toEqual([['dtype'], []]);
	});

	it('shows a suite scored under another hash as not comparable, and ranks the rest by the usual one', () => {
		const table = evalTable([
			run(0, [0.5, 0.5], [1, 1], 1),
			run(1, [0.9, 0.9], [1, 1], 1, { hashes: { zh: 'h-zh-with-a-new-task' } }),
			run(2, [0.4, 0.4], [1, 1], 1)
		]);

		expect(table.columns.zh.hash).toBe('h-zh');
		expect(table.rows[1].cells.zh).toEqual({
			value: 0.9,
			otherHash: 'h-zh-with-a-new-task',
			rank: null,
			heat: null
		});
		expect(table.rows.map((r) => r.cells.zh.rank)).toEqual([1, null, 2]);
		// A mean over every top accuracy suite needs a rank in each.
		expect(table.rows.map((r) => r.accuracyRank)).toEqual([1, null, 2]);
		expect(table.rows[1].bpbRank).toBe(1);
	});

	it('has no rows for runs with no suite score, and no rank or tint in a column of one', () => {
		const training: EvalTableRun = { row: 0, ordinal: 0, values: { 'summary.loss': 2.1 } };
		const table = evalTable([training, run(1, [0.5, 0.5], [1, 1], 1)]);

		expect(table.rows.map((r) => r.row)).toEqual([1]);
		expect(table.rows[0].cells.zh).toMatchObject({ rank: 1, heat: null });
		expect(evalTable([training])).toEqual({ columns: {}, top: [], rows: [] });
	});
});
