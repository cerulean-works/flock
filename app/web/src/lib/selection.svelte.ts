/* eslint-disable svelte/prefer-svelte-reactivity -- the selection is replaced, never changed in place */
/**
 * Rows checked in a list (D130): the Datasets tab's folder listing and the Models tab's list.
 *
 * The list sets `keys`, its rows in order, as they load. A click toggles a row, a shift-click
 * the range from the last click, as the Storage tab does. Rows that leave the list (moved,
 * deleted) are dropped with `drop`.
 */

import { clickSelect } from './storage.ts';

/** A dialog opened from the selection bar or a row's menu. */
export type RowAction = 'move' | 'delete' | 'rename';

export class Selection {
	/** The selectable rows, in the order they're listed. */
	keys = $state.raw<readonly string[]>([]);
	/** The rows checked; replaced, never changed in place. */
	selected = $state.raw<ReadonlySet<string>>(new Set());
	/** The dialog open, from the bar or a row's menu, and the rows it acts on. */
	action = $state<RowAction | null>(null);
	/** Kept once the dialog closes, so its text stays put while it animates closed. */
	targets = $state.raw<readonly string[]>([]);
	#anchor: string | null = null;

	get size() {
		return this.selected.size;
	}

	/** The checked rows, in listing order. */
	readonly chosen = $derived(this.keys.filter((k) => this.selected.has(k)));

	/** Whether every row is checked. */
	readonly all = $derived(this.keys.length > 0 && this.keys.every((k) => this.selected.has(k)));

	/** Whether some rows, not all, are checked. */
	readonly some = $derived(this.selected.size > 0 && !this.all);

	has = (key: string) => this.selected.has(key);

	/** A click on a row's checkbox, with shift held or not. */
	click = (key: string, shift = false) => {
		this.selected = clickSelect(this.selected, this.keys, key, this.#anchor, shift);
		this.#anchor = key;
	};

	/** The header's checkbox: every row, or none once all are checked. */
	toggleAll = () => {
		this.selected = this.all ? new Set() : new Set(this.keys);
	};

	/** Open a dialog on some rows: those checked, or one row from its menu. */
	act = (action: RowAction, targets: readonly string[] = this.chosen) => {
		if (!targets.length) return;
		this.targets = targets;
		this.action = action;
	};

	/** A dialog closing: `action` is no longer open. */
	close = (action: RowAction) => {
		if (this.action === action) this.action = null;
	};

	clear = () => {
		this.selected = new Set();
		this.#anchor = null;
	};

	/** Rows that left the list: out of the selection too. */
	drop = (keys: Iterable<string>) => {
		const out = new Set(this.selected);
		for (const k of keys) out.delete(k);
		this.selected = out;
	};

	/** Escape clears, unless a dialog or menu is open to take it. */
	onkeydown = (e: KeyboardEvent) => {
		if (e.key !== 'Escape' || e.defaultPrevented || !this.selected.size) return;
		if (document.querySelector('[role="dialog"], [role="alertdialog"], [role="menu"]')) return;
		this.clear();
	};
}
