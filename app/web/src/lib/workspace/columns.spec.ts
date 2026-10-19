import { describe, expect, it } from 'vitest';
import { columnDef } from '#lib/data/snapshot.ts';
import {
	type ColumnFolder,
	NO_COLUMN_PREFS,
	arrangeColumns,
	columnTree,
	filterTree,
	folderIds,
	folderState,
	leafKeys,
	moveColumn,
	moveGroup,
	parseColumnPrefs,
	parseHidden
} from './columns.ts';

const COLUMNS = [
	'state',
	'name',
	'created_at',
	'config.lr',
	'config.model.depth',
	'config.model.width',
	'config.model.encoder.heads',
	'summary.eval.acc',
	'summary.loss'
].map((k) => columnDef(k, k === 'state' ? 'state' : 'num'));

/** The tree as nested names, for readable expectations. */
const shape = (nodes: ReturnType<typeof columnTree> | ColumnFolder['children']): unknown[] =>
	nodes.map((n) => (n.kind === 'folder' ? { [n.name]: shape(n.children) } : n.name));

describe('columnTree', () => {
	it('nests columns by group, then by dotted label', () => {
		expect(shape(columnTree(COLUMNS))).toEqual([
			{ Run: ['state', 'id', 'created'] },
			{ Config: ['lr', { model: ['depth', 'width', { encoder: ['heads'] }] }] },
			{ Summary: [{ eval: ['acc'] }, 'loss'] }
		]);
	});

	it('gives folders unique ids and the hideable columns below them', () => {
		const [run, config] = columnTree(COLUMNS);
		expect(run.id).toBe('info');
		expect(run.keys).toEqual(['created_at']);
		const model = config.children[1] as ColumnFolder;
		expect(model.id).toBe('config/model');
		expect(model.keys).toEqual([
			'config.model.depth',
			'config.model.width',
			'config.model.encoder.heads'
		]);
		expect(config.keys).toHaveLength(4);
	});

	it('locks the state and id columns', () => {
		const [run] = columnTree(COLUMNS);
		expect(run.children.map((c) => c.kind === 'leaf' && c.locked)).toEqual([true, true, false]);
	});

	it('lists every folder id', () => {
		expect(folderIds(columnTree(COLUMNS))).toEqual([
			'info',
			'config',
			'config/model',
			'config/model/encoder',
			'summary',
			'summary/eval'
		]);
	});
});

describe('filterTree', () => {
	const tree = columnTree(COLUMNS);

	it('keeps matching columns and the folders on their way', () => {
		const out = filterTree(tree, 'MODEL.d');
		expect(shape(out)).toEqual([{ Config: [{ model: ['depth'] }] }]);
		expect((out[0] as ColumnFolder).keys).toEqual(['config.model.depth']);
	});

	it('matches on the whole key', () => {
		expect(shape(filterTree(tree, 'summary.'))).toEqual([{ Summary: [{ eval: ['acc'] }, 'loss'] }]);
	});

	it('keeps everything for an empty query, and nothing without a match', () => {
		expect(filterTree(tree, '  ')).toEqual(tree);
		expect(filterTree(tree, 'nope')).toEqual([]);
	});

	it('leaves locked columns out of folder keys', () => {
		const [run] = filterTree(tree, 'a') as ColumnFolder[];
		expect(run.keys).toEqual(['created_at']);
	});
});

describe('folderState', () => {
	const keys = ['a', 'b'];
	it('is all, none or some', () => {
		expect(folderState(keys, new Set())).toBe('all');
		expect(folderState(keys, new Set(['a', 'b', 'c']))).toBe('none');
		expect(folderState(keys, new Set(['b']))).toBe('some');
	});
});

describe('parseHidden', () => {
	it('reads a list of keys, without locked or repeated ones', () => {
		expect(parseHidden(['config.lr', 'state', 'config.lr', 3, 'name'])).toEqual(['config.lr']);
	});

	it('reads anything else as nothing hidden', () => {
		expect(parseHidden(null)).toEqual([]);
		expect(parseHidden({ a: 1 })).toEqual([]);
	});
});

describe('parseColumnPrefs', () => {
	it('reads the hidden keys saved before columns could be moved', () => {
		expect(parseColumnPrefs(['config.lr', 'state'])).toEqual({
			hidden: ['config.lr'],
			order: [],
			groups: []
		});
	});

	it('reads hidden keys, column order and group order', () => {
		const raw = {
			hidden: ['summary.loss', 'name'],
			order: ['config.lr', 4, 'config.lr', 'created_at'],
			groups: ['summary', 'nope', 'info', 'summary'],
			extra: true
		};
		expect(parseColumnPrefs(raw)).toEqual({
			hidden: ['summary.loss'],
			order: ['config.lr', 'created_at'],
			groups: ['summary', 'info']
		});
	});

	it('reads anything else as the defaults', () => {
		for (const raw of [null, 'x', 3, { hidden: 'a', order: {}, groups: null }])
			expect(parseColumnPrefs(raw)).toEqual(NO_COLUMN_PREFS);
	});
});

describe('arrangeColumns', () => {
	const keys = (prefs: Partial<typeof NO_COLUMN_PREFS>, columns = COLUMNS) =>
		arrangeColumns(columns, { ...NO_COLUMN_PREFS, ...prefs }).map((i) => columns[i].key);

	it('keeps schema order by default', () => {
		expect(keys({})).toEqual(COLUMNS.map((c) => c.key));
	});

	it('orders groups, and columns within their group', () => {
		expect(
			keys({ groups: ['summary'], order: ['summary.loss', 'config.model.width', 'config.lr'] })
		).toEqual([
			'summary.loss',
			'summary.eval.acc',
			'state',
			'name',
			'created_at',
			'config.model.width',
			'config.model.depth',
			'config.model.encoder.heads',
			'config.lr'
		]);
	});

	it('puts columns and groups missing from the order after the ordered ones', () => {
		const columns = [
			...COLUMNS,
			columnDef('config.model.dropout', 'num'),
			columnDef('config.wd', 'num')
		];
		expect(keys({ order: ['config.lr', 'config.model.width'] }, columns).slice(3, 10)).toEqual([
			'config.lr',
			'config.model.width',
			'config.model.depth',
			'config.model.encoder.heads',
			'config.model.dropout',
			'config.wd',
			'summary.eval.acc'
		]);
	});

	it('pins the locked columns first in the Run group', () => {
		expect(keys({ order: ['created_at', 'name', 'state'] }).slice(0, 3)).toEqual([
			'state',
			'name',
			'created_at'
		]);
	});

	it('keeps a folder together, where its first column is', () => {
		const columns = ['config.a.x', 'config.b', 'config.a.y'].map((k) => columnDef(k, 'num'));
		expect(keys({}, columns)).toEqual(['config.a.x', 'config.a.y', 'config.b']);
	});

	it('ignores saved keys and groups this page lacks', () => {
		expect(
			keys({ order: ['config.gone', 'summary.loss'], groups: ['summary'] }).slice(0, 2)
		).toEqual(['summary.loss', 'summary.eval.acc']);
	});
});

describe('moveColumn', () => {
	it('moves a column among its folder', () => {
		expect(moveColumn(COLUMNS, [], 'config/model', 1, 0)).toEqual([
			'state',
			'name',
			'created_at',
			'config.lr',
			'config.model.width',
			'config.model.depth',
			'config.model.encoder.heads',
			'summary.eval.acc',
			'summary.loss'
		]);
	});

	it('moves a folder with all of its columns', () => {
		const order = moveColumn(COLUMNS, [], 'config', 1, 0)!;
		expect(order.slice(3, 7)).toEqual([
			'config.model.depth',
			'config.model.width',
			'config.model.encoder.heads',
			'config.lr'
		]);
	});

	it('keeps the locked columns first', () => {
		expect(moveColumn(COLUMNS, [], 'info', 2, 0)).toBeNull();
		expect(moveColumn(COLUMNS, [], 'info', 0, 2)).toBeNull();
	});

	it('is null when nothing moves', () => {
		expect(moveColumn(COLUMNS, [], 'config', 0, 0)).toBeNull();
		expect(moveColumn(COLUMNS, [], 'summary', 1, 5)).toBeNull();
		expect(moveColumn(COLUMNS, [], 'nope', 0, 1)).toBeNull();
	});

	it('keeps the places of saved columns this page lacks', () => {
		const order = moveColumn(COLUMNS, ['config.gone', 'config.lr'], 'summary', 1, 0)!;
		expect(order[0]).toBe('config.gone');
		expect(order.slice(-2)).toEqual(['summary.loss', 'summary.eval.acc']);
	});

	it('lands where asked once arranged', () => {
		const order = moveColumn(COLUMNS, [], 'config', 0, 1)!;
		const tree = columnTree(arrangeColumns(COLUMNS, { order, groups: [] }).map((i) => COLUMNS[i]));
		expect(shape(tree)[1]).toEqual({
			Config: [{ model: ['depth', 'width', { encoder: ['heads'] }] }, 'lr']
		});
		expect(leafKeys(tree)).toEqual(order);
	});
});

describe('moveGroup', () => {
	it('moves a group', () => {
		expect(moveGroup(COLUMNS, [], 'summary', 1)).toEqual(['info', 'summary', 'config']);
		const groups = ['summary'] as const;
		const arranged = arrangeColumns(COLUMNS, { order: [], groups }).map((i) => COLUMNS[i]);
		expect(moveGroup(arranged, groups, 'info', 2)).toEqual(['summary', 'config', 'info']);
	});

	it('is null when nothing moves', () => {
		expect(moveGroup(COLUMNS, [], 'info', 0)).toBeNull();
		expect(moveGroup(COLUMNS, [], 'summary', 9)).toBeNull();
	});
});
