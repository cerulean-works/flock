import { describe, expect, it } from 'vitest';
import { GROUP_HUES } from '#lib/chart/grouping.ts';
import type { Proposal, RunRow } from '#lib/groups.ts';
import {
	autoPrompts,
	axisSummary,
	cellTint,
	createBody,
	layoutGrid,
	orderedAxes,
	proposalColors,
	repeatText,
	strengths,
	ungroupedCount
} from './propose.ts';

const proposal = (over: Partial<Proposal> = {}): Proposal => ({
	id: 'p1',
	name: 'Tokenizer sweep',
	why: 'Same model, only the tokenizer changes',
	runs: ['a', 'b', 'c'],
	filter: null,
	axes: ['config.tokenizer'],
	values: { 'config.tokenizer': ['bpe', 'unigram'] },
	grid: null,
	best_key: null,
	best_order: null,
	best: null,
	repeat_key: null,
	...over
});

describe('strengths', () => {
	it('ranks lowest best as strongest when ascending', () => {
		expect(strengths([{ best: 2 }, { best: 1 }, { best: 3 }], 'asc')).toEqual([0.5, 1, 0]);
	});

	it('ranks highest best as strongest when descending', () => {
		expect(strengths([{ best: 2 }, { best: 1 }, { best: 3 }], 'desc')).toEqual([0.5, 0, 1]);
	});

	it('ranks, so an outlier does not flatten the rest', () => {
		expect(strengths([{ best: 1 }, { best: 1.1 }, { best: 1.2 }, { best: 9 }], 'asc')).toEqual([
			1,
			2 / 3,
			1 / 3,
			0
		]);
		expect(strengths([{ best: 2 }, { best: 2 }], 'asc')).toEqual([1, 1]);
	});

	it('leaves cells without a value, or an unranked grid, untinted', () => {
		expect(strengths([{ best: null }, { best: 4 }], 'asc')).toEqual([null, 1]);
		expect(strengths([{ best: 1 }, { best: 2 }], null)).toEqual([null, null]);
		expect(strengths([{ best: NaN }], 'asc')).toEqual([null]);
	});
});

describe('layoutGrid', () => {
	it('lays a two-axis grid out by rows, with gaps and the best run marked', () => {
		const layout = layoutGrid(
			{
				x: 'config.lr',
				y: 'config.bs',
				xs: [0.001, 0.003],
				ys: [32, 64],
				cells: [
					{ x: 0.001, y: 32, runs: 3, best: 2.1, run: 'r1' },
					{ x: 0.003, y: 32, runs: 3, best: 1.9, run: 'r2' },
					{ x: 0.001, y: 64, runs: 2, best: 2.4, run: 'r3' }
				]
			},
			'asc',
			'r2'
		);
		expect(layout.ys).toEqual([32, 64]);
		expect(layout.rows[0].map((c) => c?.run)).toEqual(['r1', 'r2']);
		expect(layout.rows[1][1]).toBeNull();
		expect(layout.rows[0][1]?.isBest).toBe(true);
		expect(layout.rows[0][1]?.strength).toBe(1);
		expect(layout.rows[1][0]?.strength).toBe(0);
		expect([layout.covered, layout.possible]).toEqual([3, 4]);
	});

	it('makes one row of a one-axis grid and finds the best without a named run', () => {
		const layout = layoutGrid(
			{
				x: 'config.tokenizer',
				y: null,
				xs: ['bpe', 'unigram'],
				ys: null,
				cells: [
					{ x: 'bpe', y: null, runs: 2, best: 0.7, run: 'a' },
					{ x: 'unigram', y: null, runs: 2, best: 0.9, run: 'b' }
				]
			},
			'desc'
		);
		expect(layout.rows).toHaveLength(1);
		expect(layout.rows[0].map((c) => c?.isBest)).toEqual([false, true]);
	});
});

describe('cellTint', () => {
	it('mixes more of the hue into better cells', () => {
		expect(cellTint('#2a78d6', 0)).toBe('color-mix(in oklab, #2a78d6 10%, transparent)');
		expect(cellTint('#2a78d6', 1)).toBe('color-mix(in oklab, #2a78d6 65%, transparent)');
		expect(cellTint('#2a78d6', null)).toBe('color-mix(in oklab, #2a78d6 18%, transparent)');
	});
});

describe('proposalColors', () => {
	it('takes the hues no saved group has first, then wraps', () => {
		const used = [GROUP_HUES.light[0].toUpperCase()];
		const colors = proposalColors(9, used);
		expect(colors[0]).toBe(GROUP_HUES.light[1]);
		expect(colors[6]).toBe(GROUP_HUES.light[7]);
		expect(colors[7]).toBe(GROUP_HUES.light[0]);
		expect(colors[8]).toBe(GROUP_HUES.light[1]);
	});
});

describe('createBody', () => {
	it('sends the runs, color and name, and leaves out what the proposal lacks', () => {
		expect(createBody(proposal(), '#eb6834')).toEqual({
			name: 'Tokenizer sweep',
			runs: ['a', 'b', 'c'],
			color: '#eb6834'
		});
	});

	it('sends the filter and best by, and an edited name', () => {
		const p = proposal({
			filter: { group: 'tok' },
			best_key: 'summary.val/loss',
			best_order: null
		});
		expect(createBody(p, '#1baf7a', '  Tokenizers  ')).toEqual({
			name: 'Tokenizers',
			runs: ['a', 'b', 'c'],
			color: '#1baf7a',
			filter: { group: 'tok' },
			best_key: 'summary.val/loss',
			best_order: 'asc'
		});
		expect(createBody(proposal({ filter: {} }), '#1baf7a', ' ').name).toBe('Tokenizer sweep');
	});

	it('starts from the body the API made, keeping its deduplicated name', () => {
		const p = proposal({
			create: {
				name: 'Tokenizer sweep (2)',
				runs: ['a', 'b'],
				filter: null,
				best_key: 'summary.loss',
				best_order: 'desc'
			}
		});
		expect(createBody(p, '#2a78d6', 'Tokenizer sweep (2)')).toEqual({
			name: 'Tokenizer sweep (2)',
			runs: ['a', 'b'],
			color: '#2a78d6',
			best_key: 'summary.loss',
			best_order: 'desc'
		});
	});
});

describe('ungroupedCount', () => {
	it('adds the runs of left-out proposals that no kept one holds', () => {
		const all = [{ runs: ['a', 'b'] }, { runs: ['b', 'c'] }, { runs: ['d'] }];
		expect(ungroupedCount(5, all, all)).toBe(5);
		expect(ungroupedCount(5, all, [all[0]])).toBe(7);
		expect(ungroupedCount(0, all, [])).toBe(4);
	});
});

describe('repeatText', () => {
	const cells = (...runs: number[]) => ({
		x: 'k',
		y: null,
		xs: [],
		ys: null,
		cells: runs.map((n) => ({ x: n, y: null, runs: n, best: null, run: null }))
	});

	it('reads how many repeats each configuration has', () => {
		expect(repeatText({ repeat_key: 'config.seed', grid: cells(3, 3) })).toBe('3 seeds each');
		expect(repeatText({ repeat_key: 'config.seed', grid: cells(2, 3) })).toBe('2–3 seeds each');
		expect(repeatText({ repeat_key: 'config.seed', grid: null })).toBe('averaged over seeds');
		expect(repeatText({ repeat_key: null, grid: cells(3) })).toBeNull();
	});
});

describe('autoPrompts', () => {
	const run = (state: string, cells: Record<string, unknown>): RunRow => ({
		id: state,
		name: state,
		state,
		created_at: null,
		group: null,
		cells
	});

	it('names a key that varies and offers to leave out crashed runs', () => {
		const rows = [
			run('finished', { 'config.optimizer': 'adam', 'config.x': 1 }),
			run('crashed', { 'config.optimizer': 'sgd', 'config.x': 1 }),
			run('finished', { 'config.optimizer': 'adam', 'config.x': 1 })
		];
		expect(autoPrompts(rows)).toEqual([
			'Split by optimizer',
			'Leave out crashed runs',
			'Only runs from this week'
		]);
	});

	it('falls back when the runs say little', () => {
		expect(autoPrompts([])).toEqual([
			'Split by optimizer',
			'Only finished runs',
			'Only runs from this week'
		]);
	});
});

describe('axisSummary', () => {
	it('keeps a few values, or any strings, as chips', () => {
		expect(axisSummary([1, 2, 3])).toEqual({ kind: 'chips', values: [1, 2, 3] });
		const names = Array.from({ length: 10 }, (_, i) => `m${i}`);
		expect(axisSummary(names).kind).toBe('chips');
	});

	it('reads many numbers as a range, on a log scale across decades', () => {
		const lrs = [1e-5, 1e-4, 1e-3, 2e-5, 3e-5, 4e-5, 5e-5, 6e-5, 7e-5];
		const s = axisSummary(lrs);
		expect(s).toMatchObject({ kind: 'range', lo: 1e-5, hi: 1e-3, n: 9, log: true });
		if (s.kind === 'range') expect(s.at[1]).toBeCloseTo(0.5);
		const linear = axisSummary([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
		expect(linear).toMatchObject({ kind: 'range', log: false });
		if (linear.kind === 'range') expect(linear.at[5]).toBe(0.5);
	});
});

describe('orderedAxes', () => {
	it('lists the grid axes first', () => {
		const grid = { x: 'c', y: 'a', xs: [], ys: [], cells: [] };
		expect(orderedAxes({ axes: ['a', 'b', 'c'], grid })).toEqual(['c', 'a', 'b']);
		expect(orderedAxes({ axes: ['a', 'b'], grid: null })).toEqual(['a', 'b']);
	});
});
