/**
 * Which table columns a viewer shows, and the folder tree they're picked from. Column keys are
 * dotted paths (`config.model.depth`), so each group (Run, Config, Summary) is a folder whose
 * label path is split on `.` into nested folders.
 */
import type { ColumnDef } from '#lib/types.ts';

/** Columns every table shows: a run's state and id. */
export const LOCKED_COLUMNS: ReadonlySet<string> = new Set(['state', 'name']);

const GROUP_LABELS: Record<ColumnDef['group'], string> = {
	info: 'Run',
	config: 'Config',
	summary: 'Summary'
};

export interface ColumnLeaf {
	kind: 'leaf';
	/** Last segment of the column's label. */
	name: string;
	key: string;
	/** Always shown. */
	locked: boolean;
}

export interface ColumnFolder {
	kind: 'folder';
	/** Unique in the tree: the group, then the label path (`config/model/encoder`). */
	id: string;
	name: string;
	children: ColumnNode[];
	/** Every column below it that can be hidden, in table order. */
	keys: string[];
}

export type ColumnNode = ColumnFolder | ColumnLeaf;

function folder(id: string, name: string): ColumnFolder {
	return { kind: 'folder', id, name, children: [], keys: [] };
}

/**
 * The columns as folders: one per group, then one per dotted label segment. Folders and
 * columns keep table order (a folder sits where its first column does).
 */
export function columnTree(columns: readonly Pick<ColumnDef, 'key' | 'label' | 'group'>[]) {
	const roots: ColumnFolder[] = [];
	const folders = new Map<string, ColumnFolder>();
	const at = (id: string, name: string, parent: ColumnFolder | null) => {
		let f = folders.get(id);
		if (!f) {
			f = folder(id, name);
			folders.set(id, f);
			if (parent) parent.children.push(f);
			else roots.push(f);
		}
		return f;
	};
	for (const col of columns) {
		const locked = LOCKED_COLUMNS.has(col.key);
		let parent = at(col.group, GROUP_LABELS[col.group], null);
		const path = [parent];
		const parts = col.label.split('.').filter(Boolean);
		const name = parts.pop() ?? col.label;
		let id = col.group;
		for (const part of parts) {
			id += `/${part}`;
			parent = at(id, part, parent);
			path.push(parent);
		}
		parent.children.push({ kind: 'leaf', name, key: col.key, locked });
		if (!locked) for (const f of path) f.keys.push(col.key);
	}
	return roots;
}

/**
 * The tree cut down to columns whose key contains `query` (any case), with the folders on
 * their way. An empty query keeps everything.
 */
export function filterTree(nodes: readonly ColumnNode[], query: string): ColumnNode[] {
	const q = query.trim().toLowerCase();
	if (!q) return [...nodes];
	const out: ColumnNode[] = [];
	for (const n of nodes) {
		if (n.kind === 'leaf') {
			if (n.key.toLowerCase().includes(q)) out.push(n);
			continue;
		}
		const children = filterTree(n.children, q);
		if (!children.length) continue;
		const keys = children.flatMap((c) => (c.kind === 'folder' ? c.keys : c.locked ? [] : [c.key]));
		out.push({ ...n, children, keys });
	}
	return out;
}

/** Every folder id in a tree, for opening them all (as a search does). */
export function folderIds(nodes: readonly ColumnNode[]): string[] {
	return nodes.flatMap((n) => (n.kind === 'folder' ? [n.id, ...folderIds(n.children)] : []));
}

/** Whether a folder's columns are all shown, none are, or some are (its checkbox). */
export function folderState(keys: readonly string[], hidden: ReadonlySet<string>) {
	const off = keys.filter((k) => hidden.has(k)).length;
	return off === 0 ? 'all' : off === keys.length ? 'none' : 'some';
}

/** Hidden columns read back from storage: a list of column keys, locked ones dropped. */
export function parseHidden(raw: unknown): string[] {
	if (!Array.isArray(raw)) return [];
	return [
		...new Set(raw.filter((k): k is string => typeof k === 'string' && !LOCKED_COLUMNS.has(k)))
	];
}
