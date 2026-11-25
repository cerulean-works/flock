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
		'summary.t1/acc_raw': acc[0] - 0.1,
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
		const table = evalTable([run(0, [0.5, 0.7], [1.2, 0.8], 0.6), run(1, [0.4, 0.6], [1, 1], 0.7)]);

		// `qa` sits inside `zh`; `base` has no score, so what it holds is on top.
		expect(table.top).toEqual(['zh', 'code', 'qa:bpb']);
		expect(table.columns.zh).toEqual({
			suite: 'zh',
			scale: 'accuracy',
			hash: 'h-zh',
			children: ['qa']
		});
		expect(table.columns.qa.scale).toBe('accuracy');
		// `code` is bits per byte though its name doesn't say so: its score is its task's bpb.
		expect(table.columns.code.scale).toBe('bpb');
		expect(table.columns['qa:bpb'].scale).toBe('bpb');
	});

	it('reads a perplexity suite as lower is better, and leaves a scale it cannot tell unranked', () => {
		const a = run(0, [0.5, 0.5], [1, 1], 1);
		const b = run(1, [0.5, 0.5], [1, 1], 1);
		Object.assign(a.values, {
			'summary.suites/ppl': 12,
			'summary.p1/perplexity': 12,
			'config.suite_members.ppl': ['p1'],
			'config.suite_hashes.ppl': 'h-ppl',
			// Neither a mean of a lower-is-better metric nor within its member's accuracies.
			'summary.suites/odd': 7,
			'summary.o1/f1': 7,
			'config.suite_members.odd': ['o1'],
			'config.suite_hashes.odd': 'h-odd'
		});
		Object.assign(b.values, {
			'summary.suites/ppl': 15,
			'summary.p1/perplexity': 15,
			'config.suite_members.ppl': ['p1'],
			'config.suite_hashes.ppl': 'h-ppl'
		});
		const table = evalTable([a, b]);

		expect(table.columns.ppl.scale).toBe('perplexity');
		expect(table.rows.map((r) => r.cells.ppl.rank)).toEqual([1, 2]);
		expect(table.columns.odd.scale).toBeNull();
		expect(table.rows[0].cells.odd).toEqual({ value: 7, otherHash: null, rank: null, heat: null });
		// The mean rank leaves out a suite of unknown scale.
		expect(table.rows[0].meanRank).toBe(1);
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

		// One mean over every top suite: zh, code and qa:bpb.
		expect(table.rows[0].meanRank).toBeCloseTo(5 / 3);
		expect(table.rows.map((r) => r.meanRank).slice(1)).toEqual([1, 3]);
	});

	it('leaves a run scored under other settings out of every rank, naming what differs', () => {
		const table = evalTable([
			run(0, [0.5, 0.5], [1, 1], 1, { settings: { max_length: 131072, add_bos: true } }),
			run(1, [0.9, 0.9], [0.5, 0.5], 0.5, { settings: { dtype: 'float32', batch_tokens: 8192 } }),
			run(2, [0.4, 0.4], [1.1, 1.1], 1.1)
		]);

		// A model's own maximum length and its family's BOS rule are no reason to leave it out.
		expect(table.rows.map((r) => r.differs)).toEqual([[], ['dtype', 'batch_tokens'], []]);
		expect(table.rows.map((r) => r.bos)).toEqual([true, false, false]);
		expect(table.rows[1].cells.zh).toEqual({ value: 0.9, otherHash: null, rank: null, heat: null });
		expect(table.rows[1].meanRank).toBeNull();
		// The best of the others is first: the FP32 run isn't counted.
		expect(table.rows.map((r) => r.cells.zh.rank)).toEqual([1, null, 2]);
	});

	it('takes the most common settings whole, a tie to the oldest run', () => {
		// Each differs from one never used in a different setting: the oldest run's set wins.
		const table = evalTable([
			run(5, [0.5, 0.5], [1, 1], 1, { settings: { dtype: 'float32' } }),
			run(3, [0.4, 0.4], [1, 1], 1, { settings: { attention: 'eager' } }),
			run(4, [0.4, 0.4], [1, 1], 1, { settings: { batch_tokens: 8192 } })
		]);

		expect(table.rows.map((r) => r.differs)).toEqual([
			['dtype', 'attention'],
			[],
			['attention', 'batch_tokens']
		]);
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
		// A mean over every top suite needs a rank in each.
		expect(table.rows.map((r) => r.meanRank)).toEqual([1, null, 4 / 3]);
	});

	it('ranks a cell only under a known hash, and shows a score only hidden runs have', () => {
		const a = run(0, [0.5, 0.5], [1, 1], 1);
		const b = run(1, [0.4, 0.4], [1, 1], 1);
		delete a.values['config.suite_hashes.zh'];
		b.values['config.suite_hashes.zh'] = null;
		const fp32 = run(2, [0.9, 0.9], [1, 1], 1, { settings: { dtype: 'float32' } });
		Object.assign(fp32.values, {
			'summary.suites/solo': 0.3,
			'summary.s1/acc_raw': 0.3,
			'config.suite_members.solo': ['s1'],
			'config.suite_hashes.solo': 'h-solo'
		});
		const table = evalTable([a, b, fp32]);

		expect(table.rows.map((r) => r.cells.zh)).toEqual([
			{ value: 0.5, otherHash: null, rank: null, heat: null },
			{ value: 0.4, otherHash: null, rank: null, heat: null },
			{ value: 0.9, otherHash: null, rank: null, heat: null }
		]);
		// Only the hidden FP32 run scored `solo`: its score shows, under its own hash, unranked.
		expect(table.columns.solo.hash).toBe('h-solo');
		expect(table.rows[2].cells.solo).toEqual({
			value: 0.3,
			otherHash: null,
			rank: null,
			heat: null
		});
		// Neither `solo` nor `zh` has a ranked cell, so the mean is over the other two.
		expect(table.meanOver).toEqual(['code', 'qa:bpb']);
		expect(table.rows.map((r) => r.meanRank)).toEqual([1, 1, null]);
	});

	it('has no rows for runs with no suite score, and no rank or tint in a column of one', () => {
		const training: EvalTableRun = { row: 0, ordinal: 0, values: { 'summary.loss': 2.1 } };
		const table = evalTable([training, run(1, [0.5, 0.5], [1, 1], 1)]);

		expect(table.rows.map((r) => r.row)).toEqual([1]);
		expect(table.rows[0].cells.zh).toMatchObject({ rank: 1, heat: null });
		expect(evalTable([training])).toEqual({ columns: {}, top: [], meanOver: [], rows: [] });
	});

	it('gives no scale to a suite two scales fit exactly, or to one that holds itself', () => {
		const a = run(0, [0.5, 0.5], [1, 1], 1);
		const b = run(1, [0.4, 0.4], [1, 1], 1);
		for (const r of [a, b])
			Object.assign(r.values, {
				// Its one task's bits per byte equal its accuracy: either reading fits.
				'summary.suites/even': 0.5,
				'summary.e1/acc_raw': 0.5,
				'summary.e1/bpb': 0.5,
				'config.suite_members.even': ['e1'],
				'config.suite_hashes.even': 'h-even',
				// Its bits per byte fit exactly; its accuracies only bound it, which isn't enough.
				'summary.suites/wide': 0.6,
				'summary.w1/acc_raw': 0.2,
				'summary.w1/acc_uncond': 0.9,
				'summary.w1/bpb': 0.6,
				'config.suite_members.wide': ['w1'],
				'config.suite_hashes.wide': 'h-wide',
				// `z` holds itself and `a`: only `z` is a top column, whatever the names.
				'summary.suites/z': 0.5,
				'summary.suites/a': 0.5,
				'summary.a1/acc_raw': 0.5,
				'config.suite_members.z': ['z', 'a'],
				'config.suite_members.a': ['a1'],
				'config.suite_hashes.z': 'h-z',
				'config.suite_hashes.a': 'h-a',
				'summary.suites/loop': 1,
				'config.suite_members.loop': ['loop'],
				'config.suite_hashes.loop': 'h-loop'
			});
		const table = evalTable([a, b]);

		expect(table.columns.even.scale).toBeNull();
		expect(table.columns.wide.scale).toBe('bpb');
		expect(table.columns.loop.scale).toBeNull();
		// Held only by itself, it is still a column, unranked.
		expect(table.top).toContain('loop');
		expect(table.top).toContain('z');
		expect(table.top).not.toContain('a');
		expect(table.columns.z.children).toEqual(['z', 'a']);
		expect(table.rows[0].cells.loop).toEqual({ value: 1, otherHash: null, rank: null, heat: null });
		expect(table.rows.map((r) => r.cells.even.rank)).toEqual([null, null]);
	});
});
