import { describe, expect, it } from 'vitest';
import { near, parseSearch, searchScore } from './search.ts';

describe('parseSearch', () => {
	it('splits words and quoted phrases', () => {
		expect(parseSearch(' Eval "Bank V1"  x ')).toEqual({
			phrases: ['bank v1'],
			words: ['eval', 'x']
		});
		expect(parseSearch('a "open phrase')).toEqual({ phrases: ['open phrase'], words: ['a'] });
	});
});

describe('near', () => {
	it('counts edits, a swap as one', () => {
		expect(near('bnak', 'bank', 1)).toBe(true);
		expect(near('bak', 'bank', 1)).toBe(true);
		expect(near('bxyk', 'bank', 1)).toBe(false);
	});
});

describe('searchScore', () => {
	const s = (q: string, label: string, kw: string[] = []) => searchScore(q, label, kw);

	it('finds words in any order, lower than in order', () => {
		expect(s('bank eval', 'bank-eval')).toBeGreaterThan(0);
		expect(s('eval bank', 'bank-eval')).toBeGreaterThan(0);
		expect(s('eval bank', 'bank-eval')).toBeLessThan(s('bank eval', 'bank-eval'));
	});

	it('ignores separators inside a word', () => {
		expect(s('bankeval', 'bank-eval')).toBeGreaterThan(0);
		expect(s('webshare', 'web_share 0.6')).toBeGreaterThan(0);
	});

	it('forgives a typo, ranked last', () => {
		expect(s('bnak', 'bank-eval')).toBeGreaterThan(0);
		expect(s('bnak', 'bank-eval')).toBeLessThan(s('eval bank', 'bank-eval'));
		expect(s('bnk', 'zzz')).toBe(0);
		expect(s('xyz', 'bank-eval')).toBe(0);
	});

	it('keeps a quoted phrase whole', () => {
		expect(s('"bank eval"', 'bank eval v1')).toBeGreaterThan(0);
		expect(s('"eval bank"', 'bank eval v1')).toBe(0);
		expect(s('"cerulean bank"', 'cerulean-bank')).toBeGreaterThan(0);
	});

	it('matches keywords too', () => {
		expect(s('pretrain bank', 'bank-eval', ['pretrain/bank-eval'])).toBeGreaterThan(0);
	});
});
