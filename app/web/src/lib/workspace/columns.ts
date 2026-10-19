/**
 * Which table columns a viewer shows, in what order, and the folder tree they're picked from.
 * Column keys are dotted paths (`config.model.depth`), so each group (Run, Config, Summary) is a
 * folder whose label path is split on `.` into nested folders.
 */
import type { ColumnDef, ColumnGroup } from '#lib/types.ts';
import { arrange, mergeOrder, moveKey } from './layout.ts';

export type { ColumnGroup };

type Column = Pick<ColumnDef, 'key' | 'label' | 'group'>;

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
export function columnTree(columns: readonly Column[]) {
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

/** Every column key in a tree, in its order: the table's order. */
export function leafKeys(nodes: readonly ColumnNode[]): string[] {
	return nodes.flatMap((n) => (n.kind === 'folder' ? leafKeys(n.children) : [n.key]));
}

/** How the viewer set up a project's table, as kept in this browser. */
export interface ColumnPrefs {
	hidden: readonly string[];
	/** Column keys in the order they were arranged; columns logged since go after them. */
	order: readonly string[];
	/** Groups in the order they were arranged. */
	groups: readonly ColumnGroup[];
}

export const NO_COLUMN_PREFS: ColumnPrefs = { hidden: [], order: [], groups: [] };

const isGroup = (g: unknown): g is ColumnGroup => typeof g === 'string' && g in GROUP_LABELS;

const uniqueStrings = (v: unknown): string[] =>
	Array.isArray(v) ? [...new Set(v.filter((s): s is string => typeof s === 'string'))] : [];

/**
 * Column settings read back from storage. Those saved before columns could be moved are the
 * list of hidden keys alone; anything malformed is dropped rather than trusted.
 */
export function parseColumnPrefs(raw: unknown): ColumnPrefs {
	if (Array.isArray(raw)) return { ...NO_COLUMN_PREFS, hidden: parseHidden(raw) };
	if (!raw || typeof raw !== 'object') return NO_COLUMN_PREFS;
	const r = raw as Record<string, unknown>;
	return {
		hidden: parseHidden(r.hidden),
		order: uniqueStrings(r.order),
		groups: uniqueStrings(r.groups).filter(isGroup)
	};
}

/**
 * Indices into `columns` in the viewer's order: groups as arranged, then within each its
 * columns as arranged, the locked ones first. Columns and groups not in a saved order go after
 * the ordered ones, in schema order; a folder's columns stay together, where its first one is.
 */
export function arrangeColumns(
	columns: readonly Column[],
	prefs: Pick<ColumnPrefs, 'order' | 'groups'>
): number[] {
	const groups = arrange([...new Set(columns.map((c) => c.group))], (g) => g, prefs.groups);
	const rank = new Map(groups.map((g, i) => [g, i]));
	const pin = (i: number) => (LOCKED_COLUMNS.has(columns[i].key) ? i - columns.length : 0);
	const idx = arrange(
		columns.map((_, i) => i),
		(i) => columns[i].key,
		prefs.order
	).toSorted(
		(a, b) => rank.get(columns[a].group)! - rank.get(columns[b].group)! || pin(a) - pin(b)
	);
	const at = new Map(columns.map((c, i) => [c.key, i]));
	return leafKeys(columnTree(idx.map((i) => columns[i]))).map((k) => at.get(k)!);
}

/** The folder with this id in a tree, if any. */
export function findFolder(nodes: readonly ColumnNode[], id: string): ColumnFolder | null {
	for (const n of nodes) {
		if (n.kind !== 'folder') continue;
		if (n.id === id) return n;
		const found = findFolder(n.children, id);
		if (found) return found;
	}
	return null;
}

/**
 * The column order to save once the child at `from` of folder `parent` (a column, or a folder
 * with all of its own) moved to `to`, among the `arranged` columns. Locked columns keep their
 * places at the front; null when nothing moves.
 */
export function moveColumn(
	arranged: readonly Column[],
	saved: readonly string[],
	parent: string,
	from: number,
	to: number
): string[] | null {
	const tree = columnTree(arranged);
	const folder = findFolder(tree, parent);
	if (!folder) return null;
	const kids = folder.children;
	const free = kids.findIndex((n) => n.kind === 'folder' || !n.locked);
	const lead = free === -1 ? kids.length : free;
	if (from < lead || from >= kids.length) return null;
	to = Math.max(lead, Math.min(kids.length - 1, to));
	if (to === from) return null;
	kids.splice(to, 0, ...kids.splice(from, 1));
	return mergeOrder(saved, leafKeys(tree));
}

/** The group order to save once `group` moved to `to` among the `arranged` columns' groups. */
export function moveGroup(
	arranged: readonly Column[],
	saved: readonly ColumnGroup[],
	group: ColumnGroup,
	to: number
): ColumnGroup[] | null {
	const groups = [...new Set(arranged.map((c) => c.group))];
	const from = groups.indexOf(group);
	const moved = moveKey(groups, from, to);
	if (from === -1 || moved[from] === groups[from]) return null;
	return mergeOrder(saved, moved).filter(isGroup);
}
