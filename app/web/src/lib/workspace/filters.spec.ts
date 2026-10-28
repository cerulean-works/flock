import { describe, expect, it } from 'vitest';
import {
	countConditions,
	describeFilters,
	flockFilter,
	opsFor,
	ownedBy,
	parseValue,
	readFilters,
	setOwner,
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

describe('My runs', () => {
	const alice = { key: 'owner', op: 'eq', value: 3 } as const;

	it('adds an owner in place of any other, and takes only its own away', () => {
		const sam = { key: 'owner', op: 'eq', value: 4 } as const;
		const on = setOwner([sam, ...grouped], 3, true);
		expect(on).toEqual([alice, ...grouped]);
		expect(ownedBy(on, 3)).toBe(true);
		expect(ownedBy(on, 4)).toBe(false);
		expect(setOwner(on, 3, false)).toEqual(grouped);
		expect(setOwner([sam], 3, false)).toEqual([sam]);
	});

	it('keeps a lone group of any of several filters as it was', () => {
		const any = [grouped[1]];
		expect(setOwner(any, 3, true)).toEqual([alice, grouped[1]]);
		expect(setOwner(setOwner(any, 3, true), 3, false)).toEqual(any);
		// Owners inside groups are the viewer's own filters, not "My runs".
		expect(ownedBy([{ match: 'any', filters: [alice, grouped[0]] }], 3)).toBe(false);
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
