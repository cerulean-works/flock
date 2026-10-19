import { describe, expect, it } from 'vitest';
import { EMPTY_LAYOUT, arrange, mergeOrder, moveKey, parseLayout } from './layout.ts';

describe('parseLayout', () => {
	it('reads a layout saved before sections could be moved', () => {
		const saved = { order: { train: ['train/loss', 'train/acc'] }, height: { train: 240 } };
		expect(parseLayout(saved)).toEqual({
			sections: [],
			order: { train: ['train/loss', 'train/acc'] },
			height: { train: 240 }
		});
	});

	it('reads the section order', () => {
		expect(parseLayout({ sections: ['eval', 'train'], order: {}, height: {} }).sections).toEqual([
			'eval',
			'train'
		]);
	});

	it('drops what is malformed', () => {
		expect(parseLayout(null)).toBe(EMPTY_LAYOUT);
		expect(parseLayout('x')).toBe(EMPTY_LAYOUT);
		expect(
			parseLayout({
				sections: ['eval', 3],
				order: { a: 'nope', b: ['m', null] },
				height: { a: 'tall', b: 300 }
			})
		).toEqual({ sections: ['eval'], order: { b: ['m'] }, height: { b: 300 } });
	});
});

describe('arrange', () => {
	const id = (s: string) => s;

	it('puts saved keys first, in their order, and the rest after in default order', () => {
		expect(arrange(['charts', 'train', 'eval', 'system'], id, ['eval', 'train'])).toEqual([
			'eval',
			'train',
			'charts',
			'system'
		]);
	});

	it('ignores saved keys that are not there', () => {
		expect(arrange(['a', 'b', 'c'], id, ['gone', 'c'])).toEqual(['c', 'a', 'b']);
	});

	it('keeps the default order without a saved one', () => {
		expect(arrange(['b', 'a'], id, undefined)).toEqual(['b', 'a']);
		expect(arrange(['b', 'a'], id, [])).toEqual(['b', 'a']);
	});

	it('sorts by key', () => {
		const items = [{ k: 'x' }, { k: 'y' }];
		expect(arrange(items, (i) => i.k, ['y'])).toEqual([{ k: 'y' }, { k: 'x' }]);
	});
});

describe('moveKey', () => {
	it('moves a key forward and back', () => {
		expect(moveKey(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a']);
		expect(moveKey(['a', 'b', 'c'], 2, 0)).toEqual(['c', 'a', 'b']);
	});

	it('clamps the destination and ignores a missing source', () => {
		expect(moveKey(['a', 'b'], 0, 9)).toEqual(['b', 'a']);
		expect(moveKey(['a', 'b'], -1, 0)).toEqual(['a', 'b']);
	});
});

describe('mergeOrder', () => {
	it('is the arrangement when everything saved is shown', () => {
		expect(mergeOrder(['a', 'b', 'c'], ['c', 'a', 'b'])).toEqual(['c', 'a', 'b']);
	});

	it('keeps keys that are not shown in their places', () => {
		// A run's page shows only `train` and `eval`; `system` and `charts` stay where they were.
		expect(mergeOrder(['charts', 'train', 'system', 'eval'], ['eval', 'train'])).toEqual([
			'charts',
			'eval',
			'system',
			'train'
		]);
	});

	it('adds keys that were never saved', () => {
		expect(mergeOrder([], ['b', 'a'])).toEqual(['b', 'a']);
		expect(mergeOrder(['x', 'a'], ['new', 'a'])).toEqual(['x', 'new', 'a']);
	});
});
