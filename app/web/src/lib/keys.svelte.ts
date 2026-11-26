import type { Attachment } from 'svelte/attachments';

/**
 * The keyboard layer (stage 260): what the page in view offers the palette (Cmd+K), filled by
 * the page and emptied when it goes. The keys themselves are in KeyboardLayer.
 */
export const keyboard = $state<{
	/** The runs on the page, to jump to. */
	runs: { label: string; href: string }[];
	/** The page's views, to switch between. */
	views: { label: string; key: string; set: () => void }[];
	/** Opens the page's filter, when it has one. */
	filter: (() => void) | null;
}>({ runs: [], views: [], filter: null });

/** A key meant for a field, or one with a modifier: not the layer's to take. */
export function typing(e: KeyboardEvent): boolean {
	if (e.metaKey || e.ctrlKey || e.altKey) return true;
	const t = e.target;
	return t instanceof Element && !!t.closest('input, textarea, select, [contenteditable]');
}

/** A dialog, menu or list box is open, and takes the keys itself. */
export function layerOpen(): boolean {
	return !!document.querySelector(
		'[role="dialog"], [role="alertdialog"], [role="menu"], [role="listbox"]'
	);
}

/**
 * j and k (and the arrows, once a row is picked) move a cursor over a list's rows, those marked
 * `data-keyrow` inside the element; Enter opens the row's first link, x clicks its checkbox,
 * Esc lets go. The row in the cursor carries `data-cursor`, for its style.
 */
export function listKeys(): Attachment<HTMLElement> {
	return (node) => {
		let cursor = -1;
		const rows = () => [...node.querySelectorAll<HTMLElement>('[data-keyrow]')];
		const mark = (list: HTMLElement[]) => {
			list.forEach((row, i) => row.toggleAttribute('data-cursor', i === cursor));
			list[cursor]?.scrollIntoView({ block: 'nearest' });
		};
		const onkeydown = (e: KeyboardEvent) => {
			if (e.defaultPrevented || typing(e) || layerOpen()) return;
			const list = rows();
			if (!list.length) return;
			const down = e.key === 'j' || (cursor >= 0 && e.key === 'ArrowDown');
			const up = e.key === 'k' || (cursor >= 0 && e.key === 'ArrowUp');
			if (down || up) {
				e.preventDefault();
				cursor = Math.max(0, Math.min(list.length - 1, cursor < 0 ? 0 : cursor + (down ? 1 : -1)));
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
