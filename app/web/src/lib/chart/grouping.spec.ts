import { describe, expect, it } from 'vitest';
import {
	bestRows,
	chartStyle,
	GROUP_HUES,
	MISSING,
	NO_GROUP_HUE,
	orderValues,
	shade,
	valueKey,
	type StyledRun
} from './grouping.ts';

const OPTS = { groupBy: null, colorBy: null, band: 'std' } as const;
const [BLUE, ORANGE, GREEN] = GROUP_HUES.light;

/** Perceived lightness of a color, enough to tell shades apart. */
function lightness(hex: string) {
	const n = parseInt(hex.slice(1), 16);
	return 0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255);
}

describe('orderValues', () => {
	it('orders numbers ascending, whatever their spacing', () => {
		expect(orderValues([3e-4, 1e-4, 1e-3, 1e-4]).map((v) => v.value)).toEqual([1e-4, 3e-4, 1e-3]);
	});

	it('orders anything else by label, naturally, with no value last', () => {
		expect(orderValues(['run-10', undefined, 'run-9', 'adam']).map((v) => v.label)).toEqual([
			'adam',
			'run-9',
			'run-10',
			'none'
		]);
		expect(orderValues([null, 2, 1]).map((v) => v.key)).toEqual([
			valueKey(1),
			valueKey(2),
			MISSING
		]);
	});
});

describe('shade', () => {
	it('is the hue itself halfway, fainter below and stronger above', () => {
		expect(shade(BLUE, 0.5)).toBe(BLUE);
		expect(lightness(shade(BLUE, 0))).toBeGreaterThan(lightness(BLUE));
		expect(lightness(shade(BLUE, 1))).toBeLessThan(lightness(BLUE));
	});

	it('runs the other way on a dark page, so the strongest stands out from it', () => {
		const hue = GROUP_HUES.dark[0];
		expect(lightness(shade(hue, 0, true))).toBeLessThan(lightness(hue));
		expect(lightness(shade(hue, 1, true))).toBeGreaterThan(lightness(hue));
	});

	it('clamps', () => {
		expect(shade(BLUE, 2)).toBe(shade(BLUE, 1));
		expect(shade(BLUE, NaN)).toBe(BLUE);
	});
});

describe('chartStyle', () => {
	const runs: StyledRun[] = [
		{ name: 'a', group: 'adam', color: 0.1 },
		{ name: 'b', group: 'sgd', color: 0.1 },
		{ name: 'c', group: 'adam', color: 0.3 },
		{ name: 'd', group: 'sgd', color: 0.2 },
		{ name: 'e', group: 'lion', color: 0.3 }
	];

	it('is nothing to draw differently without a Group by, a Color by or means', () => {
		expect(chartStyle(runs, OPTS)).toBeNull();
	});

	it('gives each group a hue, blue then orange then green, in value order', () => {
		const s = chartStyle(runs, { ...OPTS, groupBy: 'opt' })!;
		expect(s.colors).toEqual([BLUE, GREEN, BLUE, GREEN, ORANGE]);
		expect(s.legend.map((l) => [l.label, l.color])).toEqual([
			['opt adam', BLUE],
			['opt lion', ORANGE],
			['opt sgd', GREEN]
		]);
	});

	it('shades a group’s runs faint to strong by Color by, one value one shade everywhere', () => {
		const s = chartStyle(runs, { ...OPTS, groupBy: 'opt', colorBy: 'lr' })!;
		// lr takes 0.1, 0.2 and 0.3: faintest, the hue itself, strongest.
		expect(s.colors).toEqual([
			shade(BLUE, 0),
			shade(GREEN, 0),
			shade(BLUE, 1),
			shade(GREEN, 0.5),
			shade(ORANGE, 1)
		]);
		expect(lightness(s.colors![0])).toBeGreaterThan(lightness(s.colors![2]));
	});

	it('shades one blue by Color by alone, and names its values in the legend', () => {
		const s = chartStyle(runs, { ...OPTS, colorBy: 'lr' })!;
		expect(s.colors).toEqual([0, 0, 1, 0.5, 1].map((t) => shade(BLUE, t)));
		expect(s.legend.map((l) => l.label)).toEqual(['lr 0.1', 'lr 0.2', 'lr 0.3']);
	});

	it('leaves runs without a group gray, and without a Color by value unshaded', () => {
		const s = chartStyle(
			[{ name: 'a', group: 'x', color: 1 }, { name: 'b' }, { name: 'c', group: 'x', color: 2 }],
			{
				...OPTS,
				groupBy: 'opt',
				colorBy: 'lr'
			}
		)!;
		expect(s.colors![1]).toBe(NO_GROUP_HUE.light);
		expect(s.legend.map((l) => l.label)).toEqual(['opt x', 'opt none']);
	});

	it('averages runs of one configuration into a mean, named by its values', () => {
		const repeats: StyledRun[] = [
			{ name: 'a', group: 'adam', color: 0.1, config: 'adam|0.1' },
			{ name: 'b', group: 'adam', color: 0.1, config: 'adam|0.1' },
			{ name: 'c', group: 'adam', color: 0.3, config: 'adam|0.3' },
			{ name: 'd', group: 'sgd', color: 0.1, config: null }
		];
		const s = chartStyle(repeats, { ...OPTS, groupBy: 'opt', colorBy: 'lr' })!;
		expect(s.means).toEqual([{ rows: [0, 1], label: 'opt adam · lr 0.1 · mean of 2' }]);
		// Without either key, means are named after their first run, in the run palette.
		const plain = chartStyle(repeats, OPTS)!;
		expect(plain.colors).toBeNull();
		expect(plain.legend).toEqual([]);
		expect(plain.means[0].label).toBe('a · mean of 2');
	});
});

describe('bestRows', () => {
	const run = (group: unknown, value: unknown, config: string | null = null) => ({
		group,
		config,
		value
	});

	it('keeps the best run of each group, by the metric and its order', () => {
		const runs = [run(16, 0.5), run(16, 0.3), run(32, 0.4), run(32, 0.9)];
		expect([...bestRows(runs, 'asc')].sort()).toEqual([1, 2]);
		expect([...bestRows(runs, 'desc')].sort()).toEqual([0, 3]);
	});

	it('treats every run as one group without a Group by', () => {
		const runs = [run(undefined, 0.5), run(undefined, 0.2), run(undefined, 0.4)];
		expect([...bestRows(runs, 'asc')]).toEqual([1]);
	});

	it('picks the configuration with the best mean when repeats are averaged', () => {
		// a: 0.1 and 0.9 (mean 0.5); b: 0.3 and 0.4 (mean 0.35). The lone best run is in a.
		const runs = [run(1, 0.1, 'a'), run(1, 0.9, 'a'), run(1, 0.3, 'b'), run(1, 0.4, 'b')];
		expect([...bestRows(runs, 'asc')].sort()).toEqual([2, 3]);
	});

	it('never picks a run without a number, and drops a group with none', () => {
		const runs = [run(1, null), run(1, 0.7), run(2, 'n/a'), run(2, Number.NaN)];
		expect([...bestRows(runs, 'asc')]).toEqual([1]);
	});
});
