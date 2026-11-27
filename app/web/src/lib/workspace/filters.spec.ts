import { describe, expect, it } from 'vitest';
import type { ColumnKind } from '#lib/types.ts';
import {
	chipSegments,
	dropChip,
	flipJoiner,
	joinSegments,
	chipParts,
	countConditions,
	describeFilters,
	flockFilter,
	opsFor,
	parseValue,
	readFilters,
	readColumnFilter,
	setColumnFilter,
	columnFilterKind,
	valueText,
	writeFilters,
	type People,
	type RunFilter
} from './filters.ts';

/** `state is running and (config.lr < 0.001 or config.opt is adam)` */
const grouped: RunFilter[] = [
	{ key: 'state', op: 'eq', value: 'running' },
	{
		match: 'any',
		filters: [
			{ key: 'config.lr', op: 'lt', value: 0.001 },
			{ key: 'config.opt', op: 'eq', value: 'adam' }
		]
	}
];

const people: People = new Map(
	[
		{ id: 3, name: 'Alice', email: 'alice@lab.test', avatar_url: null },
		{ id: 4, name: 'Sam', email: 'sam@lab.test', avatar_url: null },
		{ id: 5, name: 'Sam', email: 'sam.lee@lab.test', avatar_url: null }
	].map((p) => [p.id, p])
);

describe('parseValue', () => {
	it('types values by column kind', () => {
		expect(parseValue('0.01', 'num')).toEqual({ value: 0.01 });
		expect(parseValue('abc', 'num')).toEqual({ error: 'abc isn’t a number' });
		expect(parseValue('1e-4', 'str')).toEqual({ value: '1e-4' });
		expect(parseValue('true', 'bool')).toEqual({ value: true });
		expect(parseValue('yes', 'bool')).toHaveProperty('error');
		expect(parseValue('  ', 'str')).toEqual({ error: 'Enter a value' });
	});

	it('reads dates as UTC seconds', () => {
		expect(parseValue('2026-10-01', 'time')).toEqual({ value: Date.UTC(2026, 9, 1) / 1000 });
		expect(parseValue('1700000000', 'time')).toEqual({ value: 1700000000 });
		expect(valueText(Date.UTC(2026, 9, 1) / 1000, 'time')).toBe('2026-10-01');
	});

	it('reads users as the members they name', () => {
		expect(parseValue('Alice@lab.test', 'user', people)).toEqual({ value: 3 });
		expect(parseValue('alice', 'user', people)).toEqual({ value: 3 });
		expect(parseValue('sam', 'user', people)).toEqual({
			error: '2 members are named sam: use an email'
		});
		expect(parseValue('bob@lab.test', 'user', people)).toEqual({
			error: 'No member is bob@lab.test'
		});
		// Someone who has left: by id.
		expect(parseValue('9', 'user', people)).toEqual({ value: 9 });
		expect(valueText(3, 'user', people)).toBe('alice@lab.test');
		expect(valueText(9, 'user', people)).toBe('9');
	});

	it('guesses for lists and unknown columns', () => {
		expect(parseValue('baseline', 'json')).toEqual({ value: 'baseline' });
		expect(parseValue('3', undefined)).toEqual({ value: 3 });
		expect(parseValue('false', undefined)).toEqual({ value: false });
	});
});

describe('opsFor', () => {
	it('offers what the kind supports', () => {
		expect(opsFor('state').map((o) => o.op)).toEqual(['eq', 'ne']);
		expect(opsFor('num').map((o) => o.op)).toContain('lt');
		expect(opsFor('str').map((o) => o.op)).toContain('contains');
		expect(opsFor('json')[0]).toEqual({ op: 'eq', label: 'includes' });
		expect(opsFor('user')).toEqual([
			{ op: 'eq', label: 'is' },
			{ op: 'ne', label: 'is not' },
			{ op: 'exists', label: 'is set' },
			{ op: 'missing', label: 'is not set' }
		]);
	});
});

describe('URL round trip', () => {
	it('keeps valid filters and drops the rest', () => {
		const filters = [
			{ key: 'config.lr', op: 'lt' as const, value: 0.01 },
			{ key: 'summary.acc', op: 'exists' as const, value: null }
		];
		const param = writeFilters(filters);
		expect(readFilters(param)).toEqual(filters);
		expect(writeFilters([])).toBeNull();
		expect(readFilters('not json')).toEqual([]);
		expect(readFilters('[["a","bogus",1],["",  "eq", 1],["b","eq",{}],["c","eq","x"]]')).toEqual([
			{ key: 'c', op: 'eq', value: 'x' }
		]);
	});
});

describe('URL round trip with groups', () => {
	it('encodes groups as {"all"|"any": [...]}', () => {
		const param = writeFilters(grouped);
		expect(JSON.parse(param!)).toEqual([
			['state', 'eq', 'running'],
			{
				any: [
					['config.lr', 'lt', 0.001],
					['config.opt', 'eq', 'adam']
				]
			}
		]);
		expect(readFilters(param)).toEqual(grouped);
	});

	it('keeps nested groups and a lone top-level group', () => {
		const filters: RunFilter[] = [
			{
				match: 'any',
				filters: [
					{ key: 'tags', op: 'eq', value: 'baseline' },
					{
						match: 'all',
						filters: [
							{ key: 'summary.acc', op: 'exists', value: null },
							{ key: 'config.lr', op: 'gte', value: 0.1 }
						]
					}
				]
			}
		];
		expect(readFilters(writeFilters(filters))).toEqual(filters);
	});

	it('drops malformed and empty groups', () => {
		expect(
			readFilters(
				JSON.stringify([
					{ any: [] },
					{ none: [['a', 'eq', 1]] },
					{ all: [['a', 'eq', 1]], any: [] },
					{ any: 'x' },
					{ all: [['a', 'bogus', 1], { any: [] }] },
					{
						all: [
							['b', 'eq', 2],
							['c', 'lt', {}]
						]
					}
				])
			)
		).toEqual([{ match: 'all', filters: [{ key: 'b', op: 'eq', value: 2 }] }]);
	});

	it('reads flat lists from before groups', () => {
		expect(readFilters('[["state","eq","running"],["config.lr","lt",0.01]]')).toEqual([
			{ key: 'state', op: 'eq', value: 'running' },
			{ key: 'config.lr', op: 'lt', value: 0.01 }
		]);
	});
});

describe('describing filters', () => {
	it('counts conditions inside groups', () => {
		expect(countConditions(grouped)).toBe(3);
		expect(countConditions([])).toBe(0);
	});

	it('reads as text, groups in parentheses', () => {
		expect(describeFilters(grouped)).toBe(
			'state is running and (config.lr < 0.001 or config.opt is adam)'
		);
		expect(describeFilters(grouped, () => undefined, true)).toBe(
			'state is running\nand (config.lr < 0.001 or config.opt is adam)'
		);
		// A lone group is the top level: no parentheses.
		expect(describeFilters([grouped[1]])).toBe('config.lr < 0.001 or config.opt is adam');
		expect(
			describeFilters(
				[{ key: 'created_at', op: 'gte', value: Date.UTC(2026, 9, 1) / 1000 }],
				() => 'time'
			)
		).toBe('created_at ≥ 2026-10-01');
		expect(
			describeFilters([{ key: 'owner', op: 'eq', value: 3 }], () => 'user', false, people)
		).toBe('owner is alice@lab.test');
	});
});

describe('column filters', () => {
	const lr = { key: 'config.lr', op: 'lt', value: 0.1 } as const;

	it('lists states, people, lists and named text; ranges numbers and dates', () => {
		expect(columnFilterKind('state', 'state')).toBe('values');
		expect(columnFilterKind('owner', 'user')).toBe('values');
		expect(columnFilterKind('tags', 'json')).toBe('values');
		expect(columnFilterKind('job_type', 'str')).toBe('values');
		expect(columnFilterKind('config.opt', 'str')).toBe('text');
		expect(columnFilterKind('summary.loss', 'num')).toBe('range');
		expect(columnFilterKind('created_at', 'time')).toBe('date');
	});

	it('sets one value, several as any of them, and replaces only its own', () => {
		const one = setColumnFilter([lr], 'owner', 'user', { values: ['3'] });
		expect(one).toEqual([lr, { key: 'owner', op: 'eq', value: 3 }]);
		const two = setColumnFilter(one, 'owner', 'user', { values: ['3', '4'] });
		expect(two).toEqual([
			lr,
			{
				match: 'any',
				filters: [
					{ key: 'owner', op: 'eq', value: 3 },
					{ key: 'owner', op: 'eq', value: 4 }
				]
			}
		]);
		expect(readColumnFilter(two, 'owner', 'user')).toMatchObject({
			values: ['3', '4'],
			active: true
		});
		expect(setColumnFilter(two, 'owner', 'user')).toEqual([lr]);
	});

	it('keeps a range’s ends and text, and leaves conditions it did not set', () => {
		const ne = { key: 'summary.loss', op: 'ne', value: 1 } as const;
		const set = setColumnFilter([ne], 'summary.loss', 'num', { range: { min: '0.1', max: '2' } });
		expect(set).toEqual([
			ne,
			{ key: 'summary.loss', op: 'gte', value: 0.1 },
			{ key: 'summary.loss', op: 'lte', value: 2 }
		]);
		expect(readColumnFilter(set, 'summary.loss', 'num').range).toEqual({ min: '0.1', max: '2' });
		const text = setColumnFilter([], 'config.opt', 'str', { text: ' adam ' });
		expect(text).toEqual([{ key: 'config.opt', op: 'contains', value: 'adam' }]);
		expect(readColumnFilter(text, 'config.opt', 'str').text).toBe('adam');
	});
});

describe('flockFilter', () => {
	it('is null without a search or filters', () => {
		expect(flockFilter('  ', [])).toBeNull();
	});

	it('searches names and ids literally, in any case', () => {
		expect(flockFilter('lr=3e-4 (a)', [])).toEqual({
			$or: [
				{ display_name: { $regex: '(?i)lr=3e-4 \\(a\\)' } },
				{ name: { $regex: '(?i)lr=3e-4 \\(a\\)' } }
			]
		});
	});

	it('combines filters with $and', () => {
		expect(
			flockFilter('', [
				{ key: 'state', op: 'eq', value: 'running' },
				{ key: 'config.lr', op: 'gte', value: 0.1 },
				{ key: 'summary.acc', op: 'missing', value: null },
				{ key: 'config.opt', op: 'contains', value: 'Adam' }
			])
		).toEqual({
			$and: [
				{ state: { $eq: 'running' } },
				{ 'config.lr': { $gte: 0.1 } },
				{ 'summary.acc': { $exists: false } },
				{ 'config.opt': { $regex: '(?i)Adam' } }
			]
		});
	});

	it('turns groups into $or and $and', () => {
		expect(flockFilter('', grouped)).toEqual({
			$and: [
				{ state: { $eq: 'running' } },
				{ $or: [{ 'config.lr': { $lt: 0.001 } }, { 'config.opt': { $eq: 'adam' } }] }
			]
		});
		expect(flockFilter('', [grouped[1]])).toEqual({
			$or: [{ 'config.lr': { $lt: 0.001 } }, { 'config.opt': { $eq: 'adam' } }]
		});
		expect(
			flockFilter('abc', [
				{
					match: 'any',
					filters: [
						{ key: 'a', op: 'eq', value: 1 },
						{
							match: 'all',
							filters: [
								{ key: 'b', op: 'exists', value: null },
								{ key: 'c', op: 'ne', value: 'x' }
							]
						}
					]
				}
			])
		).toEqual({
			$and: [
				{ $or: [{ display_name: { $regex: '(?i)abc' } }, { name: { $regex: '(?i)abc' } }] },
				{ $or: [{ a: { $eq: 1 } }, { $and: [{ b: { $exists: true } }, { c: { $ne: 'x' } }] }] }
			]
		});
	});

	it('unwraps groups of one and leaves out empty ones', () => {
		expect(
			flockFilter('', [
				{ match: 'any', filters: [] },
				{
					match: 'any',
					filters: [
						{ key: 'a', op: 'eq', value: 1 },
						{ match: 'all', filters: [] }
					]
				}
			])
		).toEqual({ a: { $eq: 1 } });
		expect(flockFilter('', [{ match: 'all', filters: [] }])).toBeNull();
	});
});

describe('chipParts', () => {
	const kinds: Record<string, ColumnKind> = {
		state: 'state',
		owner: 'user',
		tags: 'json',
		created_at: 'time',
		'config.lr': 'num',
		name: 'str'
	};
	const names = {
		label: (k: string) => (k === 'config.lr' ? 'lr' : k[0].toUpperCase() + k.slice(1)),
		kind: (k: string) => kinds[k],
		people: new Map([[7, { id: 7, name: 'Ada', avatar_url: null }]]) as unknown as People,
		me: 3
	};

	it('reads one value as `is` and several as `is any of`, as the table writes them', () => {
		expect(chipParts({ key: 'state', op: 'eq', value: 'finished' }, names)).toEqual({
			label: 'State',
			op: 'is',
			value: 'Finished'
		});
		expect(
			chipParts(
				{
					match: 'any',
					filters: [
						{ key: 'owner', op: 'eq', value: 3 },
						{ key: 'owner', op: 'eq', value: 7 }
					]
				},
				names
			)
		).toEqual({ label: 'Owner', op: 'is any of', value: 'Me, Ada' });
		expect(chipParts({ key: 'tags', op: 'eq', value: 'base' }, names).op).toBe('includes');
	});

	it('words ranges, dates and text', () => {
		expect(chipParts({ key: 'config.lr', op: 'gte', value: 0.0012346 }, names)).toEqual({
			label: 'lr',
			op: 'at least',
			value: '0.001235'
		});
		expect(chipParts({ key: 'config.lr', op: 'lte', value: 2 }, names).op).toBe('at most');
		const nov20 = new Date(2026, 10, 20).getTime() / 1000;
		expect(chipParts({ key: 'created_at', op: 'gte', value: nov20 }, names).op).toBe('after');
		expect(chipParts({ key: 'created_at', op: 'lte', value: nov20 }, names).op).toBe('before');
		expect(chipParts({ key: 'name', op: 'contains', value: 'sweep' }, names)).toEqual({
			label: 'Name',
			op: 'contains',
			value: 'sweep'
		});
		expect(chipParts({ key: 'name', op: 'exists', value: null }, names)).toEqual({
			label: 'Name',
			op: 'is set',
			value: ''
		});
	});

	it('gives a group built in Advanced as its text alone', () => {
		expect(
			chipParts(
				{
					match: 'any',
					filters: [
						{ key: 'config.lr', op: 'lt', value: 0.01 },
						{ key: 'name', op: 'eq', value: 'a' }
					]
				},
				names
			)
		).toEqual({ label: '', op: '', value: 'config.lr < 0.01 or name is a' });
	});
});

describe('the chip row', () => {
	const running = { key: 'state', op: 'eq', value: 'running' } as const;
	const finished = { key: 'state', op: 'eq', value: 'finished' } as const;
	const states = { match: 'any' as const, filters: [running, finished] };
	const tags = { key: 'tags', op: 'contains', value: 'baseline' } as const;
	const owner = { key: 'owner', op: 'eq', value: 7 } as const;

	it('shows top-level filters as chips joined with and', () => {
		expect(chipSegments([running, owner])).toEqual([[running], [owner]]);
		expect(chipSegments([])).toEqual([]);
	});

	it("keeps one column's values as one chip", () => {
		expect(chipSegments([states])).toEqual([[states]]);
	});

	it('shows an or group inside the row as its chips', () => {
		// (state is running or finished) or tags has baseline, and owner is 7
		const filters = [{ match: 'any' as const, filters: [states, tags] }, owner];
		expect(chipSegments(filters)).toEqual([[states, tags], [owner]]);
		expect(joinSegments(chipSegments(filters))).toEqual(filters);
	});

	it('reads a top-level all group as the row itself', () => {
		expect(chipSegments([{ match: 'all', filters: [running, owner] }])).toEqual([
			[running],
			[owner]
		]);
	});

	it('switches one joiner: and joins its neighbours with or, or splits there', () => {
		const row = [[states, tags], [owner]];
		expect(flipJoiner(row, 1)).toEqual([[states, tags, owner]]);
		expect(flipJoiner(row, 0)).toEqual([[states], [tags], [owner]]);
		expect(flipJoiner(flipJoiner(row, 0), 0)).toEqual(row);
	});

	it('drops a chip, leaving a lone chip of an or group plain', () => {
		const row = [[states, tags], [owner]];
		expect(dropChip(row, 1)).toEqual([[states], [owner]]);
		expect(joinSegments(dropChip(row, 2))).toEqual([{ match: 'any', filters: [states, tags] }]);
	});
});
