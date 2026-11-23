import { describe, expect, it } from 'vitest';
import { flockFilter, type RunFilter } from './workspace/filters.ts';
import {
	bestRun,
	changes,
	classifyColumns,
	configurations,
	draftCounts,
	editGroupHref,
	formatDelta,
	fromFlockFilter,
	groupHref,
	groupName,
	groupsApi,
	groupsHref,
	inDraft,
	isRepeatName,
	lookOf,
	newGroupHref,
	ungroupedHref,
	rowsOf,
	setConditions,
	tick,
	toFlockFilter,
	viewOf,
	type Draft
} from './groups.ts';

describe('group URLs', () => {
	it('links Compare, a group, its edit page and the create page', () => {
		expect(groupsHref('acme', 'lm')).toBe('/acme/lm/groups');
		expect(ungroupedHref('acme', 'lm')).toBe('/acme/lm/groups#ungrouped');
		expect(groupHref('acme', 'lm', 7)).toBe('/acme/lm/groups/7');
		expect(editGroupHref('acme', 'lm', 7)).toBe('/acme/lm/groups/7/edit');
		expect(editGroupHref('acme', 'lm', 7, true)).toBe('/acme/lm/groups/7/edit?new=1');
		expect(newGroupHref('acme', 'lm')).toBe('/acme/lm/groups/new');
		const href = newGroupHref('acme', 'lm', {
			runs: ['a', 'b'],
			filters: [{ key: 'group', op: 'eq', value: 'sweep' }],
			name: 'sweep'
		});
		const search = new URL(href, 'http://flock.test').searchParams;
		expect(search.get('runs')).toBe('a,b');
		expect(search.get('filters')).toBe('[["group","eq","sweep"]]');
		expect(search.get('name')).toBe('sweep');
	});

	it('names the dashboard’s endpoints', () => {
		expect(groupsApi('acme', 'my lm')).toBe('/api/projects/acme/my%20lm/groups');
		expect(groupsApi('acme', 'lm', 'compare', { reference: 'r1', other: null })).toBe(
			'/api/projects/acme/lm/groups/compare?reference=r1'
		);
	});
});

describe('filters', () => {
	const cases: RunFilter[][] = [
		[],
		[{ key: 'state', op: 'eq', value: 'finished' }],
		[
			{ key: 'state', op: 'eq', value: 'finished' },
			{ key: 'config.lr', op: 'lt', value: 0.01 },
			{ key: 'name', op: 'contains', value: 'a.b' },
			{ key: 'config.x', op: 'missing', value: null }
		],
		[
			{ key: 'state', op: 'ne', value: 'failed' },
			{
				match: 'any',
				filters: [
					{ key: 'config.opt', op: 'eq', value: 'adam' },
					{ key: 'config.lr', op: 'gte', value: 0.001 }
				]
			}
		]
	];

	it('reads back what flockFilter writes', () => {
		for (const filters of cases) expect(fromFlockFilter(toFlockFilter(filters))).toEqual(filters);
		expect(toFlockFilter([])).toEqual({});
		expect(toFlockFilter(cases[1])).toEqual(flockFilter('', cases[1]));
	});

	it('reads a suggestion’s bare values and ranges on one column', () => {
		expect(fromFlockFilter({ group: 'pretrain-1b' })).toEqual([
			{ key: 'group', op: 'eq', value: 'pretrain-1b' }
		]);
		expect(fromFlockFilter({ 'config.lr': { $gt: 1, $lte: 5 } })).toEqual([
			{ key: 'config.lr', op: 'gt', value: 1 },
			{ key: 'config.lr', op: 'lte', value: 5 }
		]);
		expect(fromFlockFilter(null)).toEqual([]);
	});

	it('gives up on what the popover can’t show', () => {
		expect(fromFlockFilter({ state: { $in: ['a', 'b'] } })).toBeNull();
		expect(fromFlockFilter({ name: { $regex: '^run-.*' } })).toBeNull();
		expect(fromFlockFilter({ $nor: [] })).toBeNull();
	});

	it('sets an assistant’s conditions in place of those on their columns', () => {
		const filters: RunFilter[] = [
			{ key: 'state', op: 'eq', value: 'finished' },
			{ key: 'config.bs', op: 'gt', value: 64 }
		];
		expect(setConditions(filters, [{ key: 'config.bs', op: 'lte', value: 128 }])).toEqual([
			{ key: 'state', op: 'eq', value: 'finished' },
			{ key: 'config.bs', op: 'lte', value: 128 }
		]);
	});
});

describe('column kinds', () => {
	it('sorts keys into varies, repeat, same and unique', () => {
		const configs = [
			{
				'config.lr': 1e-4,
				'config.seed': 1,
				'config.d': 'web',
				'config.path': '/a',
				'config.m': 1
			},
			{ 'config.lr': 1e-4, 'config.seed': 2, 'config.d': 'web', 'config.path': '/b' },
			{
				'config.lr': 3e-4,
				'config.seed': 1,
				'config.d': 'web',
				'config.path': '/c',
				'config.m': 1
			},
			{ 'config.lr': 3e-4, 'config.seed': 2, 'config.d': 'web', 'config.path': '/d', 'config.m': 1 }
		];
		expect(classifyColumns(configs)).toEqual({
			'config.d': 'same',
			'config.lr': 'varies',
			'config.m': 'varies',
			'config.path': 'unique',
			'config.seed': 'repeat'
		});
		expect(classifyColumns(configs, { 'config.path': 'varies' })['config.path']).toBe('varies');
	});

	it('treats a random search’s numbers and a small group’s strings as varies', () => {
		const search = [1, 2, 3, 4].map((i) => ({ 'config.lr': i / 1000 }));
		expect(classifyColumns(search)['config.lr']).toBe('varies');
		const small = ['web-v1', 'web-v2', 'web-v3'].map((d) => ({ 'config.mix': d }));
		expect(classifyColumns(small)['config.mix']).toBe('varies');
	});

	it('knows seeds by name', () => {
		for (const key of [
			'config.seed',
			'config.data_seed',
			'config.trainer.random_state',
			'config.rng'
		])
			expect(isRepeatName(key)).toBe(true);
		expect(isRepeatName('config.seeds')).toBe(false);
	});
});

describe('configurations and the best', () => {
	const row = (id: string, cells: Record<string, unknown>) => ({ id, cells });
	const rows = [
		row('a', { 'config.lr': 1, 'config.seed': 1, 'summary.loss': 2.0 }),
		row('b', { 'config.lr': 1, 'config.seed': 2, 'summary.loss': 2.2 }),
		row('c', { 'config.lr': 2, 'config.seed': 1, 'summary.loss': 1.9 }),
		row('d', { 'config.lr': 2, 'config.seed': 2, 'summary.loss': 2.5 }),
		row('e', { 'config.lr': 3, 'config.seed': 1, 'summary.loss': NaN })
	];

	it('averages seeds and puts the best configuration first', () => {
		const kinds = classifyColumns(rows.map((r) => r.cells).map(onlyConfig));
		const configs = configurations(rows, kinds, 'summary.loss', 'asc');
		expect(configs.map((c) => c.rows.map((r) => r.id))).toEqual([['a', 'b'], ['c', 'd'], ['e']]);
		expect(configs[0].value).toBeCloseTo(2.1);
		expect(configs[0].std).toBeCloseTo(0.1);
		expect(configs[2].value).toBeNull();
		expect(bestRun(configs, 'summary.loss', 'asc')?.id).toBe('a');
	});

	it('ranks runs alone without a repeat key; NaN is never best', () => {
		const kinds = { 'config.lr': 'varies' as const };
		const configs = configurations(rows, kinds, 'summary.loss', 'desc');
		expect(configs.map((c) => c.rows[0].id)).toEqual(['d', 'b', 'a', 'c', 'e']);
		expect(bestRun(configs, 'summary.loss', 'desc')?.id).toBe('d');
		expect(
			bestRun(configurations([rows[4]], kinds, 'summary.loss', 'asc'), 'summary.loss', 'asc')
		).toBeNull();
	});
});

function onlyConfig(cells: Record<string, unknown>) {
	return Object.fromEntries(Object.entries(cells).filter(([k]) => k.startsWith('config.')));
}

describe('names', () => {
	it('names groups from what varies (D192)', () => {
		const two = [
			{ 'config.lr': 1, 'config.batch_size': 32 },
			{ 'config.lr': 1, 'config.batch_size': 64 },
			{ 'config.lr': 2, 'config.batch_size': 64 }
		];
		// Keys in order, as the API names them.
		expect(groupName(two, [])).toBe('batch_size × lr');
		const bool = [
			{ 'config.qk_norm': true },
			{ 'config.qk_norm': false },
			{ 'config.qk_norm': true }
		];
		expect(groupName(bool, [])).toBe('qk_norm on / off');
		const mix = [{ 'config.data_mix': 'web-v3' }, { 'config.data_mix': 'web-v2' }];
		expect(groupName(mix, [{ key: 'state', op: 'eq', value: 'finished' }])).toBe(
			'data_mix: web-v2, web-v3, finished'
		);
		expect(groupName([{ 'config.x': 1 }, { 'config.x': 1 }], [], 'pretrain-1b')).toBe(
			'pretrain-1b'
		);
		expect(groupName([{}], [])).toBe('1 run');
	});
});

describe('drafts (D190)', () => {
	const base: Draft = {
		matched: new Set(['a', 'b', 'c']),
		members: new Set(),
		dropped: new Set(),
		added: new Set(),
		removed: new Set(),
		editing: false
	};

	it('creates from the filters, ticks by hand and leaves out', () => {
		let d: Draft = { ...base, ...tick(base, 'x', true) };
		d = { ...d, ...tick(d, 'b', false) };
		expect(['a', 'b', 'c', 'x', 'y'].map((id) => lookOf(d, id))).toEqual([
			'filter',
			'out',
			'filter',
			'hand',
			'none'
		]);
		expect(['a', 'b', 'x'].map((id) => inDraft(d, id))).toEqual([true, false, true]);
		expect(draftCounts(d, ['a', 'b', 'c', 'x', 'y'])).toMatchObject({
			in: 3,
			filter: 2,
			hand: 1,
			out: 1
		});
		// Ticked back: no longer by hand.
		d = { ...d, ...tick(d, 'b', true) };
		expect(lookOf(d, 'b')).toBe('filter');
	});

	it('edits: members stay, new matches are offered, dropped runs can come back', () => {
		let d: Draft = {
			...base,
			editing: true,
			members: new Set(['a', 'm']),
			dropped: new Set(['c'])
		};
		expect(['a', 'm', 'b', 'c', 'z'].map((id) => lookOf(d, id))).toEqual([
			'member',
			'member',
			'new',
			'dropped',
			'none'
		]);
		expect(inDraft(d, 'b')).toBe(false);
		d = { ...d, ...tick(d, 'b', true) };
		d = { ...d, ...tick(d, 'c', true) };
		d = { ...d, ...tick(d, 'm', false) };
		expect(lookOf(d, 'm')).toBe('out');
		expect(changes(d)).toEqual({ add: ['b', 'c'], drop: ['m'] });
		// A member added by hand says so.
		expect(lookOf({ ...d, byHand: new Set(['a']) }, 'a')).toBe('hand');
	});
});

describe('display', () => {
	it('signs changes with a real minus', () => {
		expect(formatDelta(-0.0551)).toBe('−0.055');
		expect(formatDelta(0.02)).toBe('+0.020');
		expect(formatDelta(0)).toBe('0.000');
	});

	it('defaults a group’s charts to its varying keys (D189)', () => {
		expect(viewOf({}, ['config.lr'])).toEqual({
			split: null,
			color: 'config.lr',
			seeds: 'std',
			show: 'all'
		});
		expect(viewOf(undefined, ['config.bs', 'config.lr'])).toEqual({
			split: 'config.lr',
			color: 'config.bs',
			seeds: 'std',
			show: 'all'
		});
		expect(
			viewOf({ split: 'config.gone', color: 'config.lr', seeds: 'runs' }, ['config.lr'])
		).toEqual({
			split: null,
			color: 'config.lr',
			seeds: 'runs',
			show: 'all'
		});
	});

	it('groups a group’s charts by a key of few values, shaded by the one of most', () => {
		const keys = ['config.bs', 'config.dropout', 'config.lr', 'config.opt'];
		const distinct = { 'config.bs': 5, 'config.dropout': 16, 'config.lr': 17, 'config.opt': 3 };
		expect(viewOf({}, keys, distinct)).toEqual({
			split: 'config.opt',
			color: 'config.lr',
			seeds: 'std',
			show: 'all'
		});
		expect(viewOf({ split: null, color: null, show: 'best' }, keys).show).toBe('best');
		// Color by may be the seed, which varies in every run.
		expect(viewOf({ split: null, color: 'config.seed' }, keys, distinct, ['config.seed'])).toEqual({
			split: null,
			color: 'config.seed',
			seeds: 'std',
			show: 'all'
		});
	});

	it('reads a runs query as rows', () => {
		const rows = rowsOf({
			total: 2,
			offset: 0,
			limit: 2,
			ids: ['r1', 'r2'],
			columns: ['display_name', 'state', 'config.lr'],
			data: {
				display_name: ['one', null],
				state: ['running', 'finished'],
				'config.lr': [0.1, null]
			}
		});
		expect(rows.map((r) => [r.id, r.name, r.state, r.cells['config.lr']])).toEqual([
			['r1', 'one', 'running', 0.1],
			['r2', 'r2', 'finished', undefined]
		]);
	});
});
