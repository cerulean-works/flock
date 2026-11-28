import type { Attachment } from 'svelte/attachments';
import type { ColumnKind, SortOrder } from '#lib/types.ts';
import type { ColumnFilterProps } from '#lib/workspace/column-filter.ts';
import type { RunFilter } from '#lib/workspace/filters.ts';

/** A column the palette can filter or sort by, named as the table names it. */
export interface PaletteColumn {
	key: string;
	label: string;
	kind?: ColumnKind;
}

/** The page's filters, as the palette edits them (the same state as the headers' funnels). */
export interface PaletteFilters {
	columns: () => PaletteColumn[];
	/** The column's filter now: read inside the palette, so it follows each change. */
	props: (key: string) => ColumnFilterProps;
	/** Any filter in use. */
	active: () => boolean;
	/** The filters in effect, and a way to set them all (the match switch). */
	applied: () => readonly RunFilter[];
	set: (filters: RunFilter[]) => void;
	/** A chip's words after its column's name: "is any of Failed, Crashed", "≥ 0.001". */
	describe: (chip: RunFilter) => string;
	clear: () => void;
	/** The and/or builder. */
	advanced: () => void;
}

/** The runs selected on the page, and what the palette can do to them. */
export interface PaletteSelection {
	count: number;
	/** The project's tags, each with how many of the selected runs have it. */
	/** Each tag: how many of the selected runs have it, and how many runs in the project. */
	tags: () => { tag: string; on: number; runs: number }[];
	tag: (tag: string, on: boolean) => void;
	/** Takes a tag off every run in the project that has it. */
	deleteTag: (tag: string) => void;
	/** The project's saved groups, when it has them. */
	groups: () => { id: number; name: string; color: string }[] | null;
	addToGroup: (id: number) => void;
	/** Opens the New group form for them, with a name typed in the palette when there is one. */
	newGroup: (name?: string) => void;
	/** Two eval runs, to compare (on a benchmark picked next). */
	pair: { a: { id: string; label: string }; b: { id: string; label: string } } | null;
}

/** The page's runs, to tick in the palette: ticking selects them in the table too. */
export interface PalettePick {
	runs: () => { id: string; label: string; state: string; on: boolean }[];
	toggle: (id: string) => void;
}

/** The project's eval runs, to compare two of from the palette. */
export interface PaletteCompare {
	entity: string;
	project: string;
}

/** The page's sort. */
export interface PaletteSort {
	columns: () => PaletteColumn[];
	current: () => { key: string; order: SortOrder; byDefault: boolean };
	set: (key: string, order: SortOrder) => void;
	reset: () => void;
}

/**
 * The keyboard layer (stage 260): what the page in view offers the palette (Cmd+K), filled by
 * the page and emptied when it goes. The keys themselves are in KeyboardLayer.
 */
export const keyboard = $state<{
	/** The runs on the page, to jump to. */
	runs: {
		label: string;
		href: string;
		/** Where the run lives, shown beside it. */ project: string;
	}[];
	/** The page's views, to switch between. */
	views: {
		label: string;
		key: string;
		set: () => void;
		on?: boolean;
		/** Ways the view lays out (Visualize: side by side or stacked), each a row of its own. */
		layouts?: { label: string; set: () => void; on: boolean }[];
	}[];
	/** Opens the page's filter, when it has one. */
	filter: (() => void) | null;
	/** Home's own command field: Cmd+K puts the cursor there instead of opening the dialog. */
	search: (() => void) | null;
	/** The page's filters and sort, which the palette opens into (F, Shift+S). */
	filters: PaletteFilters | null;
	sort: PaletteSort | null;
	selection: PaletteSelection | null;
	compare: PaletteCompare | null;
	pick: PalettePick | null;
	/** Opens the palette, inside Filter or Sort when asked (set by the keyboard layer). */
	palette: ((at?: 'filter' | 'sort') => void) | null;
}>({
	runs: [],
	views: [],
	filter: null,
	search: null,
	filters: null,
	sort: null,
	selection: null,
	compare: null,
	pick: null,
	palette: null
});

/** A key meant for a field, or one with a modifier: not the layer's to take. */
export function typing(e: KeyboardEvent): boolean {
	if (e.metaKey || e.ctrlKey || e.altKey) return true;
	const t = e.target;
	// A field in a layer that is closing (⌘K animating out) keeps focus a moment: not typing.
	return (
		t instanceof Element &&
		!!t.closest('input, textarea, select, [contenteditable]') &&
		!t.closest('[data-state="closed"]')
	);
}

/** A dialog, menu or list box is open, and takes the keys itself. */
export function layerOpen(): boolean {
	// One closing (still animating out) has let go, with all inside it: a key pressed right after
	// closing ⌘K counts.
	return [
		...document.querySelectorAll(
			'[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]'
		)
	].some((el) => !el.closest('[data-state="closed"]'));
}

/**
 * j and k (and the arrows, once a row is picked) move a cursor over a list's rows, those marked
 * `data-keyrow` inside the element; Enter opens the row's first link, x clicks its checkbox,
 * Shift+arrows tick the rows they pass, Esc lets go. The row in the cursor carries `data-cursor`, for its style.
 */
export function listKeys(): Attachment<HTMLElement> {
	return (node) => {
		let cursor = -1;
		const rows = () => [...node.querySelectorAll<HTMLElement>('[data-keyrow]')];
		const mark = (list: HTMLElement[]) => {
			list.forEach((row, i) => row.toggleAttribute('data-cursor', i === cursor));
			list[cursor]?.scrollIntoView({ block: 'nearest' });
		};
		const tick = (row: HTMLElement | undefined) => {
			const box = row?.querySelector<HTMLElement>('[role="checkbox"]');
			if (box && box.getAttribute('aria-checked') !== 'true') box.click();
		};
		const onkeydown = (e: KeyboardEvent) => {
			if (e.defaultPrevented || typing(e) || layerOpen()) return;
			const list = rows();
			if (!list.length) return;
			const down = e.key === 'j' || (cursor >= 0 && e.key === 'ArrowDown');
			const up = e.key === 'k' || (cursor >= 0 && e.key === 'ArrowUp');
			if (down || up) {
				e.preventDefault();
				const from = cursor;
				cursor = Math.max(0, Math.min(list.length - 1, cursor < 0 ? 0 : cursor + (down ? 1 : -1)));
				// Shift+arrows tick the rows passed over, as a shift-click does.
				if (e.shiftKey) for (const i of [from, cursor]) tick(list[i]);
				mark(list);
			} else if (cursor >= 0 && e.key === 'Enter') {
				e.preventDefault();
				list[cursor]?.querySelector<HTMLElement>('a[href]')?.click();
			} else if (cursor >= 0 && e.key === 'x') {
				list[cursor]?.querySelector<HTMLElement>('[role="checkbox"]')?.click();
			} else if (cursor >= 0 && e.key === 'Escape') {
				cursor = -1;
				mark(list);
			}
		};
		// The pointer takes over from the keys.
		const onpointermove = () => {
			if (cursor < 0) return;
			cursor = -1;
			mark(rows());
		};
		window.addEventListener('keydown', onkeydown);
		node.addEventListener('pointermove', onpointermove);
		return () => {
			window.removeEventListener('keydown', onkeydown);
			node.removeEventListener('pointermove', onpointermove);
		};
	};
}

/**
 * A submenu's field takes what's typed wherever focus is in the submenu: moving the pointer over
 * its rows moves focus onto them (as menus do), which would otherwise send the letters to the
 * menu's typeahead instead of the search. Backspace edits the field too.
 */
export function typeInto(input: HTMLInputElement): () => void {
	const menu = input.closest<HTMLElement>('[role=menu]');
	if (!menu) return () => {};
	const onkeydown = (e: KeyboardEvent) => {
		if (e.target === input || e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
		const printable = e.key.length === 1 && e.key !== ' ';
		if (!printable && !(e.key === 'Backspace' && input.value)) return;
		e.preventDefault();
		e.stopPropagation();
		input.focus();
		input.value = printable ? input.value + e.key : input.value.slice(0, -1);
		input.dispatchEvent(new Event('input', { bubbles: true }));
	};
	menu.addEventListener('keydown', onkeydown, true);
	return () => menu.removeEventListener('keydown', onkeydown, true);
}
