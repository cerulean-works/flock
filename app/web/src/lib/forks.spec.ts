import { describe, expect, it } from 'vitest';
import { addedColumn, allMatch, cellMark, forkPath, parseValue, safeCast } from './forks.ts';
import type { BlendColumn } from './types.ts';

describe('forkPath', () => {
	it('puts the fork beside its source, numbered once the name is taken', () => {
		expect(forkPath('web/edu')).toBe('web/edu-fork');
		expect(forkPath('edu')).toBe('edu-fork');
		expect(forkPath('web/edu', new Set(['web/edu-fork', 'web/edu-fork-2']))).toBe('web/edu-fork-3');
	});
});

describe('safeCast', () => {
	it('allows only casts that keep every value, as the API does', () => {
		expect(safeCast('int32', 'int64')).toBe(true);
		expect(safeCast('int64', 'int32')).toBe(false);
		expect(safeCast('int32', 'double')).toBe(true);
		expect(safeCast('int64', 'double')).toBe(false); // above 2^53 a double loses digits
		expect(safeCast('uint16', 'int32')).toBe(true);
		expect(safeCast('uint32', 'int32')).toBe(false);
		expect(safeCast('string', 'large_string')).toBe(true);
		expect(safeCast('large_string', 'string')).toBe(false);
	});
});

const column = (from: BlendColumn['from'], name = 'text'): BlendColumn => ({
	name,
	type: 'string',
	nullable: true,
	from
});

describe('cellMark and allMatch', () => {
	it('marks what differs from taking the same column', () => {
		const c = column({
			a: { column: 'text' },
			b: { column: 'content' },
			c: { column: 'text', cast: true },
			d: { null: true },
			e: { value: 'x' }
		});
		expect(['a', 'b', 'c', 'd', 'e'].map((s) => cellMark(c, s))).toEqual([
			null,
			'renamed',
			'cast',
			'null',
			'value'
		]);
	});

	it('matches only when every cell takes its own column and nothing is wrong', () => {
		const same = column({ a: { column: 'text' }, b: { column: 'text' } });
		expect(allMatch([same], [])).toBe(true);
		expect(allMatch([same], ['text: a problem'])).toBe(false);
		expect(allMatch([same, column({ a: { null: true }, b: { column: 'x' } }, 'x')], [])).toBe(
			false
		);
	});
});

describe('addedColumn', () => {
	it('takes the column from its source, null elsewhere, renamed when taken', () => {
		expect(addedColumn('url', 'string', 'b', ['a', 'b'], new Set(['url']))).toEqual({
			name: 'url_2',
			type: 'string',
			nullable: true,
			from: { a: { null: true }, b: { column: 'url' } }
		});
	});
});

describe('parseValue', () => {
	it('sends numbers for number columns and text for the rest', () => {
		expect(parseValue('3', 'int64')).toBe(3);
		expect(parseValue('2.5', 'double')).toBe(2.5);
		expect(parseValue('3', 'string')).toBe('3');
		expect(parseValue('abc', 'int64')).toBe('abc'); // left for the API to refuse
		expect(parseValue('TRUE', 'bool')).toBe(true);
	});
});
