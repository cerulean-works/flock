import { describe, expect, it } from 'vitest';
import {
	EMPTY_LAYOUT,
	arrange,
	mergeOrder,
	migrateOrder,
	moveKey,
	pageStart,
	pinFirst,
	parseLayout,
	panelSmoothing,
	parseCustomPanels,
	parsePanelConfig,
	parsePlotSettings
} from './layout.ts';

describe('parseLayout', () => {
	it('reads a layout saved before sections could be moved', () => {
		const saved = { order: { train: ['train/loss', 'train/acc'] }, height: { train: 240 } };
		expect(parseLayout(saved)).toEqual({
			sections: [],
			order: { train: ['train/loss', 'train/acc'] },
			height: { train: 240 },
			rows: {},
			panels: {},
			pinned: [],
			settings: {},
			custom: []
		});
	});

	it('reads rows per section, as whole rows within bounds', () => {
		const layout = parseLayout({ rows: { a: 3, b: 2.6, c: 0, d: 99, e: '4', f: NaN } });
		expect(layout.rows).toEqual({ a: 3, b: 3, c: 1, d: 12 });
		expect(parseLayout({ rows: [3] }).rows).toEqual({});
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
		).toEqual({
			sections: ['eval'],
			order: { b: ['m'] },
			height: { b: 300 },
			rows: {},
			panels: {},
			pinned: [],
			settings: {},
			custom: []
		});
	});

	it('reads panel settings, dropping panels left with none', () => {
		const layout = parseLayout({
			panels: { loss: { log: true, yMax: 2 }, acc: { log: 'yes' }, x: null },
			order: {},
			height: {}
		});
		expect(layout.panels).toEqual({ loss: { log: true, yMax: 2 } });
	});
});

describe('parseCustomPanels', () => {
	it('keeps added panels with a custom id and at least one metric', () => {
		const loss = { id: 'custom:a', config: { metrics: ['loss', 'val/loss'], log: true } };
		expect(parseLayout({ custom: [loss] }).custom).toEqual([loss]);
		expect(
			parseCustomPanels([
				loss,
				// The same id twice, a metric's id, no metrics, a bare prefix, the wrong types.
				{ id: 'custom:a', config: { metrics: ['acc'] } },
				{ id: 'loss', config: { metrics: ['loss'] } },
				{ id: 'custom:b', config: { metrics: [], title: 'Empty' } },
				{ id: 'custom:c', config: { title: 'No metrics' } },
				{ id: 'custom:', config: { metrics: ['loss'] } },
				{ id: 3, config: { metrics: ['loss'] } },
				'custom:d',
				{ id: 'custom:e', config: { metrics: ['acc', 7, 'acc'], smoothing: 5 } }
			])
		).toEqual([loss, { id: 'custom:e', config: { metrics: ['acc'], smoothing: 0.99 } }]);
		expect(parseCustomPanels({ 'custom:a': loss })).toEqual([]);
	});
});

describe('parsePlotSettings', () => {
	it('keeps what is well formed, clamped', () => {
		const settings = {
			smoothingType: 'average',
			smoothing: 20,
			showOriginal: false,
			ignoreOutliers: true,
			forecast: true,
			xAxis: '_runtime',
			rows: 4
		};
		expect(parseLayout({ settings }).settings).toEqual(settings);
		expect(parsePlotSettings({ smoothing: 2, rows: 0.2, xAxis: ' ', showOriginal: 'no' })).toEqual({
			smoothing: 0.99,
			rows: 1
		});
		expect(parsePlotSettings(null)).toEqual({});
	});
});

describe('parsePanelConfig', () => {
	it('keeps what is well formed', () => {
		const config = {
			title: 'Loss',
			metrics: ['loss', 'val/loss'],
			xMin: 0,
			xMax: 1000,
			xLabel: 'Step',
			yMin: -1,
			yLabel: 'nats',
			ignoreOutliers: true,
			smoothing: 0.6,
			showOriginal: false,
			lineWidth: 2,
			legend: false,
			log: true,
			forecast: true,
			forecastTo: 20000
		};
		expect(parsePanelConfig(config)).toEqual(config);
		expect(parsePanelConfig({ forecast: 'yes', forecastTo: -5 })).toBeNull();
		expect(parsePanelConfig({ forecastTimes: 3 })).toEqual({ forecastTimes: 3 });
		expect(parsePanelConfig({ forecastTimes: 1 })).toBeNull();
	});

	it('drops the wrong types, blank text and unknown fields', () => {
		expect(
			parsePanelConfig({
				title: '  ',
				xLabel: ' x ',
				metrics: ['a', 3, 'a'],
				yMin: '1',
				yMax: Infinity,
				log: 1,
				color: 'red'
			})
		).toEqual({ xLabel: 'x', metrics: ['a'] });
		expect(parsePanelConfig({ metrics: [] })).toBeNull();
		expect(parsePanelConfig('x')).toBeNull();
		expect(parsePanelConfig([])).toBeNull();
	});

	it('clamps numbers to what the editor offers', () => {
		expect(parsePanelConfig({ smoothing: 3, lineWidth: 0 })).toEqual({
			smoothing: 0.99,
			lineWidth: 0.5
		});
		expect(parsePanelConfig({ smoothingType: 'average', smoothing: 500 })).toEqual({
			smoothingType: 'average',
			smoothing: 100
		});
		expect(parsePanelConfig({ smoothingType: 'gaussian', smoothing: 0 })).toEqual({
			smoothingType: 'gaussian',
			smoothing: 0.1
		});
	});

	it('reads smoothing methods, and an old weight alone as an EMA', () => {
		expect(panelSmoothing(parsePanelConfig({ smoothing: 0.5 })!)).toEqual({
			type: 'ema',
			param: 0.5
		});
		expect(parsePanelConfig({ smoothingType: 'none', smoothing: 0.5 })).toEqual({
			smoothingType: 'none'
		});
		expect(parsePanelConfig({ smoothingType: 'twema' })).toEqual({
			smoothingType: 'twema',
			smoothing: 0.6
		});
		// An unknown method is dropped; its weight is read as an EMA's.
		expect(parsePanelConfig({ smoothingType: 'median', smoothing: 0.2 })).toEqual({
			smoothing: 0.2
		});
		expect(panelSmoothing({})).toBeUndefined();
		// A weight of 0 is kept as picked: it draws unsmoothed.
		expect(parsePanelConfig({ smoothing: 0 })).toEqual({ smoothing: 0 });
		expect(panelSmoothing({ smoothing: 0 })).toEqual({ type: 'ema', param: 0 });
		expect(parsePanelConfig({ smoothingType: 'twema', smoothing: 0 })).toEqual({
			smoothingType: 'twema',
			smoothing: 0
		});
	});

	it('keeps the x axis, and the axis x bounds were set on only with bounds', () => {
		expect(parsePanelConfig({ xAxis: '_runtime', xMin: 60, xRangeAxis: '_runtime' })).toEqual({
			xAxis: '_runtime',
			xMin: 60,
			xRangeAxis: '_runtime'
		});
		expect(parsePanelConfig({ xAxis: 3, xRangeAxis: '_runtime' })).toBeNull();
	});

	it('drops bounds the wrong way round, keeping the other axis', () => {
		expect(parsePanelConfig({ xMin: 5, xMax: 5, yMin: 0, yMax: 1 })).toEqual({ yMin: 0, yMax: 1 });
		expect(parsePanelConfig({ yMin: 3, yMax: 1 })).toBeNull();
	});
});

describe('migrateOrder', () => {
	const alias = (k: string) =>
		({ 'system.gpu.0.gpu': 'system:gpu.gpu', 'system.gpu.1.gpu': 'system:gpu.gpu' })[k];

	it("maps metric keys to the panel plotting them, which takes the first one's place", () => {
		expect(migrateOrder(['system.gpu.1.gpu', 'system.cpu', 'system.gpu.0.gpu'], alias)).toEqual([
			'system:gpu.gpu',
			'system.cpu'
		]);
	});

	it('keeps panel ids and logged metric keys as they are', () => {
		expect(migrateOrder(['train/loss', 'system:gpu.gpu', 'system.gpu.0.gpu'], alias)).toEqual([
			'train/loss',
			'system:gpu.gpu'
		]);
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

describe('pageStart', () => {
	it('starts the page at `start`', () => {
		expect(pageStart(0, 8, 20)).toBe(0);
		expect(pageStart(8, 8, 20)).toBe(8);
	});

	it('keeps the first panel shown when pages grow or shrink', () => {
		expect(pageStart(12, 8, 108)).toBe(12);
		expect(pageStart(12, 4, 108)).toBe(12);
	});

	it('fills the last page with panels from the one before', () => {
		// 17–20 of 20 at 8 a page shows 13–20.
		expect(pageStart(16, 8, 20)).toBe(12);
		expect(pageStart(16, 12, 20)).toBe(8);
		expect(pageStart(16, 4, 20)).toBe(16);
	});

	it('stays in range when there are fewer panels than it was left at', () => {
		expect(pageStart(40, 8, 20)).toBe(12);
		expect(pageStart(5, 8, 3)).toBe(0);
		expect(pageStart(5, 8, 0)).toBe(0);
	});
});

describe('pinFirst', () => {
	const pinned = new Set(['c', 'e']);

	it('puts pinned keys first, each part in its own order', () => {
		expect(pinFirst(['a', 'b', 'c', 'd', 'e'], pinned)).toEqual(['c', 'e', 'a', 'b', 'd']);
	});

	it('leaves a loose key where it is', () => {
		expect(pinFirst(['a', 'b', 'c', 'd', 'e'], pinned, 'e')).toEqual(['c', 'a', 'b', 'd', 'e']);
		expect(pinFirst(['e', 'a', 'c'], pinned, 'e')).toEqual(['e', 'c', 'a']);
	});
});

describe('parseLayout pins', () => {
	it('keeps pinned ids once each', () => {
		expect(parseLayout({ pinned: ['a', 'b', 'a', 3] }).pinned).toEqual(['a', 'b']);
	});
});
