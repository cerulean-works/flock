import type { Component } from 'svelte';
import FolderInputIcon from '@lucide/svelte/icons/folder-input';
import GitCompareArrowsIcon from '@lucide/svelte/icons/git-compare-arrows';
import LayersIcon from '@lucide/svelte/icons/layers';
import PencilIcon from '@lucide/svelte/icons/pencil';
import TagIcon from '@lucide/svelte/icons/tag';
import Trash2Icon from '@lucide/svelte/icons/trash-2';
import { compareHref } from '#lib/evals.ts';
import type { Workspace } from './workspace.svelte.ts';

/** One thing to do to some runs, as a menu item or a button of the selection's bar. */
export interface RunAction {
	key: string;
	label: string;
	icon: Component;
	/** A page to go to (Compare); otherwise `run`. */
	href?: string;
	run?: () => void;
	destructive?: boolean;
}

/**
 * The rows a row's menu acts on (its ⋯ or a right-click): the selection when the row is in a
 * selection of several, so two runs picked compare from either one's menu; the row alone
 * otherwise.
 */
export function menuRows(ws: Workspace, row: number): number[] {
	const rows = ws.selectedRows;
	return rows.length > 1 && rows.includes(row) ? rows : [row];
}

/**
 * What can be done to `rows` (page rows), in one order everywhere: Compare when they are two
 * runs, Edit for one run, then Tags, Add to group, Move and Delete. `selection`: the rows are
 * the selection, which the dialogs read.
 */
export function runActions(
	ws: Workspace,
	rows: readonly number[],
	selection: boolean
): RunAction[] {
	const one = rows.length === 1;
	const open = (kind: 'edit' | 'move' | 'delete') => () =>
		selection ? ws.openDialog(kind) : ws.openDialog(kind, rows[0]);
	const out: RunAction[] = [];
	if (rows.length === 2) {
		const [a, b] = rows.map((r) => ws.page.runs[r].id);
		out.push({
			key: 'compare',
			label: 'Compare',
			icon: GitCompareArrowsIcon,
			href: compareHref(
				ws.meta.entity,
				ws.meta.project,
				a,
				`${ws.meta.entity}/${ws.meta.project}/${b}`
			)
		});
	}
	if (one) out.push({ key: 'edit', label: 'Edit run…', icon: PencilIcon, run: open('edit') });
	out.push(
		// Tags edit in a submenu beside the menu (TagsEditor), not a dialog.
		{ key: 'tag', label: 'Tags', icon: TagIcon },
		// The project's groups in a submenu, with New group… at its foot (RunMenuItems).
		{ key: 'group', label: 'Add to group', icon: LayersIcon },
		{ key: 'move', label: 'Move to project…', icon: FolderInputIcon, run: open('move') },
		{
			key: 'delete',
			label: one ? 'Delete run…' : `Delete ${rows.length} runs…`,
			icon: Trash2Icon,
			run: open('delete'),
			destructive: true
		}
	);
	return out;
}
