import { describe, expect, it } from 'vitest';
import {
	awsConfigureSnippet,
	binarySize,
	compactJson,
	formatCell,
	isNumericType,
	loadSnippet,
	opLabel,
	shortType,
	truncatedCells,
	versionDelta
} from './datasets.ts';
import type { DatasetVersion } from './types.ts';

describe('formatCell', () => {
	it.each([
		[null, 'null', 'null'],
		[1234567, '1,234,567', 'number'],
		[0.5, '0.5', 'number'],
		[true, 'true', 'bool'],
		['hello', 'hello', 'string'],
		['', '', 'string'],
		[{ $binary: 2048 }, 'binary · 2,048 bytes', 'binary'],
		[[1, 2, 3], '[1,2,3]', 'json'],
		[{ a: { b: 'c' } }, '{"a":{"b":"c"}}', 'json']
	])('%j reads %j', (value, text, kind) => {
		expect(formatCell(value)).toEqual({ text, kind });
	});

	it('shows nested binary values by their size', () => {
		expect(formatCell({ image: { $binary: 3 }, label: 1 }).text).toBe(
			'{"image":"‹binary · 3 bytes›","label":1}'
		);
	});
});

describe('binarySize', () => {
	it('only takes the exact binary shape', () => {
		expect(binarySize({ $binary: 0 })).toBe(0);
		expect(binarySize({ $binary: 1, other: 2 })).toBeNull();
		expect(binarySize({ $binary: '1' })).toBeNull();
		expect(binarySize([{ $binary: 1 }])).toBeNull();
		expect(compactJson([{ $binary: 1 }])).toBe('["‹binary · 1 byte›"]');
	});
});

describe('columns', () => {
	it('right-aligns numbers', () => {
		expect(isNumericType('int64')).toBe(true);
		expect(isNumericType('double')).toBe(true);
		expect(isNumericType('string')).toBe(false);
		expect(isNumericType('list<item: int64>')).toBe(false);
	});

	it('shortens list types', () => {
		expect(shortType('list<item: string>')).toBe('list<string>');
		expect(shortType('fixed_size_list<element: float>[512]')).toBe('fixed_size_list<float>[512]');
	});

	it('looks up cut cells by row and column', () => {
		const cut = truncatedCells([
			[0, 1],
			[3, 0]
		]);
		expect(cut.has('0:1')).toBe(true);
		expect(cut.has('1:0')).toBe(false);
	});
});

const version = (v: Partial<DatasetVersion>): DatasetVersion => ({
	version: 1,
	created_at: 0,
	author: null,
	op: 'Append',
	message: null,
	rows: null,
	bytes: null,
	fragments: null,
	tags: [],
	pruned: false,
	...v
});

describe('versionDelta', () => {
	it('gives the first version its size', () => {
		expect(versionDelta(version({ rows: 1000, bytes: 2048 }), undefined)).toBe(
			'1,000 rows · 2.0 KB'
		);
	});

	it('gives later versions what changed', () => {
		const before = version({ rows: 1000, bytes: 4096 });
		expect(versionDelta(version({ rows: 1500, bytes: 6144 }), before)).toBe('+500 rows · +2.0 KB');
		expect(versionDelta(version({ rows: 900, bytes: 4096 }), before)).toBe('−100 rows');
		expect(versionDelta(version({ rows: 1000, bytes: 4096 }), before)).toBe('');
	});

	it('says nothing while the summary is unknown', () => {
		expect(versionDelta(version({}), version({ rows: 1 }))).toBe('');
	});
});

describe('snippets', () => {
	it('names operations in words', () => {
		expect(opLabel('UpdateConfig')).toBe('update config');
		expect(opLabel('Append')).toBe('append');
		expect(opLabel(null)).toBe('internal');
	});

	it('pins a version when asked', () => {
		expect(loadSnippet('flock', 'fineweb')).toContain('load_dataset("flock/fineweb")');
		expect(loadSnippet('flock', 'fineweb', 4)).toContain(
			'load_dataset("flock/fineweb", version=4)'
		);
	});

	it('configures the AWS CLI with the endpoint', () => {
		const key = {
			access_key_id: 'FLKHABC',
			secret_access_key: 's3cr3t',
			region: 'us-east-1',
			endpoint: 'https://hub.example.test'
		};
		expect(awsConfigureSnippet(key)).toContain(
			'aws configure set endpoint_url https://hub.example.test --profile flock'
		);
		expect(awsConfigureSnippet({ ...key, endpoint: null })).not.toContain('endpoint_url');
	});
});
