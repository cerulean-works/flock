import { describe, expect, it } from 'vitest';
import { rankChoices, reachesOut } from './run-choices.ts';
import type { RunChoice } from './types.ts';

const run = (project: string, id: string, name: string, created_at = 0): RunChoice => ({
	entity: 'lab',
	project,
	id,
	display_name: name,
	created_at,
	state: 'finished',
	job_type: null,
	model: null
});
const home = { entity: 'lab', project: 'canary' };

describe('reachesOut', () => {
	it('reaches past the project from three letters', () => {
		expect(reachesOut('no')).toBe(false);
		expect(reachesOut(' n o v ')).toBe(true);
	});
});

describe('rankChoices', () => {
	it('lists the home project newest first without a search', () => {
		const runs = [run('canary', 'a1', 'old', 1), run('canary', 'a2', 'new', 2)];
		expect(rankChoices('', home, runs).map((r) => r.id)).toEqual(['a2', 'a1']);
	});

	it('ranks the best match first, wherever it is', () => {
		const runs = [
			run('canary', 'a1', 'canary-11w4'),
			run('prod', 'b1', 'nov-production-run'),
			run('prod', 'b2', 'november-sweep')
		];
		expect(rankChoices('nov production', home, runs)[0].id).toBe('b1');
	});

	it('puts the home project first among equal matches', () => {
		const runs = [run('prod', 'b1', 'baseline', 5), run('canary', 'a1', 'baseline', 1)];
		expect(rankChoices('baseline', home, runs).map((r) => r.project)).toEqual(['canary', 'prod']);
	});

	it('finds a run by its id', () => {
		const runs = [run('prod', 'zli7myil', 'canary-11w4'), run('prod', 'qq', 'other')];
		expect(rankChoices('zli7myil', home, runs)[0].id).toBe('zli7myil');
	});
});
