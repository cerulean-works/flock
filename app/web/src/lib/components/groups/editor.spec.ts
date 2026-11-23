import { describe, expect, it } from 'vitest';
import type { RunRow } from '#lib/groups.ts';
import { FALLBACK_PROMPTS, suggestPrompts } from './editor.ts';

const run = (name: string, state: string, cells: Record<string, unknown> = {}): RunRow => ({
	id: name,
	name,
	state,
	created_at: null,
	group: null,
	cells
});

describe('suggestPrompts', () => {
	it('reads a state, a varying number and a run from the project', () => {
		const rows = [
			run('lone-orca', 'running', { 'config.lr': 0.003, 'config.seed': 1 }),
			run('calm-fox', 'finished', { 'config.lr': 0.001, 'config.seed': 1 }),
			run('wise-owl', 'crashed', { 'config.lr': 0.0003, 'config.seed': 1 })
		];
		expect(suggestPrompts(rows, rows.slice(1))).toEqual([
			'Only finished runs',
			'Only lr up to 0.001',
			'Add calm-fox too'
		]);
	});

	it('takes the commonest state when no run finished', () => {
		const rows = [run('a', 'crashed'), run('b', 'running'), run('c', 'running')];
		expect(suggestPrompts(rows, [])[0]).toBe('Only running runs');
	});

	it('falls back when the project says too little', () => {
		expect(suggestPrompts([], [])).toEqual([...FALLBACK_PROMPTS]);
		const rows = [
			run('a', 'finished', { 'config.lr': 1 }),
			run('b', 'finished', { 'config.lr': 2 })
		];
		expect(suggestPrompts(rows, [])[1]).toBe(FALLBACK_PROMPTS[1]);
	});
});
