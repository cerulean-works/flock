import type { Attachment } from 'svelte/attachments';

/**
 * The hover hint: a short text that follows the pointer, in place of the browser's `title`
 * (slow, unstyled, and pinned where the pointer first stopped). One layer draws it for the whole
 * page (HintLayer, in the root layout); elements ask for it with `{@attach hint(text)}`.
 *
 * It waits a moment before showing (longer on controls than on informational marks), so a pointer crossing a table doesn't flash one per cell, then
 * stays warm: moving straight onto another hinted element shows its hint at once. Keyboard focus
 * shows it under the element, and touch never does.
 */

export const tip = $state<{ text: string; x: number; y: number; owner: Element | null }>({
	text: '',
	x: 0,
	y: 0,
	owner: null
});

// Two speeds (Linear's): controls people use every day wait longer, so crossing the sidebar or
// a toolbar doesn't flash a hint per item; informational marks (a dot, a cut-off value, an info
// icon) answer quickly, since hovering one is a question.
const CONTROL_DELAY = 600;
const INFO_DELAY = 250;
const WARM = 400;
const CONTROL =
	'a, button, input, select, textarea, label, [role="button"], [role="tab"], [role="menuitem"], [role="switch"], [role="checkbox"]';

let timer: ReturnType<typeof setTimeout> | undefined;
let cooled: ReturnType<typeof setTimeout> | undefined;
let warm = false;

function show(owner: Element, text: string, x: number, y: number) {
	tip.text = text;
	tip.x = x;
	tip.y = y;
	tip.owner = owner;
}

function hide(owner: Element) {
	clearTimeout(timer);
	if (tip.owner !== owner) return;
	tip.owner = null;
	warm = true;
	clearTimeout(cooled);
	cooled = setTimeout(() => (warm = false), WARM);
}

/** A hover hint of `text`; none when it's empty. */
export function hint(text: string | null | undefined): Attachment<HTMLElement | SVGElement> {
	return (node) => {
		if (!text) return;
		const label = text;
		const DELAY = node.matches(CONTROL) ? CONTROL_DELAY : INFO_DELAY;
		// Screen readers get the text too: as the element's name when it has none of its own (an
		// icon button), else as a description beside it.
		const named =
			node.hasAttribute('aria-label') ||
			node.hasAttribute('aria-labelledby') ||
			!!node.textContent?.trim();
		const attr = named ? 'aria-description' : 'aria-label';
		if (!node.hasAttribute(attr)) node.setAttribute(attr, label);

		const enter = (e: PointerEvent) => {
			if (e.pointerType === 'touch') return;
			clearTimeout(timer);
			if (warm) show(node, label, e.clientX, e.clientY);
			else timer = setTimeout(() => show(node, label, e.clientX, e.clientY), DELAY);
		};
		const move = (e: PointerEvent) => {
			if (tip.owner === node) {
				tip.x = e.clientX;
				tip.y = e.clientY;
			} else if (e.pointerType !== 'touch' && !warm) {
				// Still waiting: the hint appears where the pointer has come to rest.
				clearTimeout(timer);
				timer = setTimeout(() => show(node, label, e.clientX, e.clientY), DELAY);
			}
		};
		const leave = () => hide(node);
		const focus = () => {
			if (!node.matches(':focus-visible')) return;
			const box = node.getBoundingClientRect();
			show(node, label, box.left, box.bottom + 18);
		};

		node.addEventListener('pointerenter', enter as EventListener);
		node.addEventListener('pointermove', move as EventListener);
		node.addEventListener('pointerleave', leave);
		node.addEventListener('pointerdown', leave);
		node.addEventListener('focus', focus);
		node.addEventListener('blur', leave);
		return () => {
			node.removeEventListener('pointerenter', enter as EventListener);
			node.removeEventListener('pointermove', move as EventListener);
			node.removeEventListener('pointerleave', leave);
			node.removeEventListener('pointerdown', leave);
			node.removeEventListener('focus', focus);
			node.removeEventListener('blur', leave);
			if (node.getAttribute(attr) === label) node.removeAttribute(attr);
			hide(node);
		};
	};
}
