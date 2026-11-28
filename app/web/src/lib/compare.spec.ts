import { describe, expect, it } from 'vitest';
import { byDifference, pairRows, sameValue } from './compare.ts';

describe('pairRows', () => {
	it('pairs every key of both, nested ones dotted, A to Z', () => {
		const rows = pairRows({ lr: 0.1, opt: { name: 'adam' } }, { lr: 0.2, seed: 1 });
		expect(rows.map((r) => r.key)).toEqual(['lr', 'opt.name', 'seed']);
		expect(rows.find((r) => r.key === 'seed')).toMatchObject({ a: undefined, b: 1, same: false });
	});

	it('takes A − B for two numbers, and how far apart they are', () => {
		const [loss] = pairRows({ loss: 2 }, { loss: 1.5 });
		expect(loss.delta).toBe(0.5);
		expect(loss.spread).toBeCloseTo(0.25);
	});

	it('marks equal values the same, lists by their contents', () => {
		const rows = pairRows({ x: [1, 2], y: 'a' }, { x: [1, 2], y: 'a' });
		expect(rows.every((r) => r.same && r.spread === 0)).toBe(true);
	});

	it('leaves out the keys asked', () => {
		const rows = pairRows({ _step: 1, loss: 1 }, { _step: 2 }, (k) => k.startsWith('_'));
		expect(rows.map((r) => r.key)).toEqual(['loss']);
	});
});

describe('byDifference', () => {
	it('puts the most different first and the same last', () => {
		const rows = pairRows({ a: 1, b: 10, c: 'x', d: 5 }, { a: 1, b: 9, c: 'y', d: 1 });
		expect(byDifference(rows).map((r) => r.key)).toEqual(['c', 'd', 'b', 'a']);
	});
});

describe('sameValue', () => {
	it('tells null from a value', () => {
		expect(sameValue(null, 0)).toBe(false);
		expect(sameValue(null, null)).toBe(true);
	});
});
