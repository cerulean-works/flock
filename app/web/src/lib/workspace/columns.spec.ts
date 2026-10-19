import { describe, expect, it } from 'vitest';
import { columnDef } from '#lib/data/snapshot.ts';
import {
	type ColumnFolder,
	columnTree,
	filterTree,
	folderIds,
	folderState,
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
