import { groupName } from '#lib/groups.ts';
import type { Workspace } from './workspace.svelte.ts';

/**
 * **New group…** from a runs menu: the form it opens outlives the menu, so what it acts on lives
 * here, and the table renders the form once (`NewGroupForm`).
 */
export const newGroup = $state<{
	open: boolean;
	/** The ⋯ (or the right-click's point) it opens from. */
	anchor: HTMLElement | null;
	ids: string[];
	/** The name offered: what varies among the runs (D225). */
	name: string;
}>({ open: false, anchor: null, ids: [], name: '' });

/** Opens the form for some page rows, from the menu open now. */
export function openNewGroup(ws: Workspace, rows: readonly number[], anchor: HTMLElement | null) {
	// The runs' config cells, as the group editor reads runs.
	const keys = ws.meta.columns.map((c) => c.key).filter((k) => k.startsWith('config.'));
	const configs = rows.map((r) => Object.fromEntries(keys.map((k) => [k, ws.cell(r, k)])));
	newGroup.ids = rows.map((r) => ws.page.runs[r].id);
	// What varies, when that makes a name ("lr × steps"); runs apart in many ways get none.
	const name = groupName(configs, []);
	newGroup.name = / more$|created_at|command/.test(name) ? '' : name;
	newGroup.anchor = anchor;
	newGroup.open = true;
}
