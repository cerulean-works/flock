import { replaceState } from '$app/navigation';
import { page } from '$app/state';

/**
 * A settings page's table of contents (the settings sidebar): which section is in view, read by
 * the sidebar to mark its entry. The page sets it as it scrolls; the sidebar jumps with `jumpTo`.
 */
export const toc = $state<{ current: string | null }>({ current: null });

/**
 * A jump's section keeps the mark until the reader scrolls themselves: the sections it passes
 * don't take it, nor does the last one when the jump lands where the page can't scroll further.
 */
let pinned = false;

const reduced = () =>
	typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Points the address at a section without a history entry (a scroll isn't a page). */
function mark(slug: string) {
	if (page.url.hash === `#${slug}`) return;
	try {
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- the page's own address
		replaceState(`${page.url.pathname}${page.url.search}#${slug}`, page.state);
	} catch {
		// Before the router has started (a test, or the first paint): the mark is enough.
	}
}

/** Scrolls a settings page to a section (or a part of one), smoothly unless motion is reduced. */
export function jumpTo(id: string, { smooth = true } = {}) {
	const el = document.getElementById(id);
	if (!el) return false;
	const section = el.closest<HTMLElement>('[data-toc]');
	toc.current = section?.id ?? id;
	pinned = true;
	el.scrollIntoView({ behavior: smooth && !reduced() ? 'smooth' : 'auto', block: 'start' });
	mark(id);
	return true;
}

const SCROLL_KEYS = new Set(['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' ']);

/**
 * Scrollspy for a settings page's scroller: the section whose heading last crossed the top third
 * is current, and the last one once the page can't scroll further. The address follows.
 */
export function scrollspy(scroller: HTMLElement) {
	let frame = 0;
	const update = () => {
		frame = 0;
		if (pinned) return;
		const sections = [...scroller.querySelectorAll<HTMLElement>('section[data-toc]')];
		if (!sections.length) return;
		const top = scroller.getBoundingClientRect().top;
		const line = top + scroller.clientHeight / 3;
		const end = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2;
		let current = sections[0];
		for (const s of sections) if (s.getBoundingClientRect().top <= line) current = s;
		if (end && scroller.scrollTop > 0) current = sections[sections.length - 1];
		if (toc.current !== current.id) {
			toc.current = current.id;
			// Only once the page has been scrolled: arriving at the top leaves the address alone.
			if (scroller.scrollTop > 0) mark(current.id);
		}
	};
	const onscroll = () => {
		if (!frame) frame = requestAnimationFrame(update);
	};
	// The reader's own scrolling lets a jump's mark go.
	const release = (e: Event) => {
		if (e instanceof KeyboardEvent && !SCROLL_KEYS.has(e.key)) return;
		pinned = false;
	};
	scroller.addEventListener('scroll', onscroll, { passive: true });
	// (A scrollbar dragged is a press on the scroller; keys go to whatever has focus.)
	for (const type of ['wheel', 'touchmove', 'pointerdown'] as const)
		scroller.addEventListener(type, release, { passive: true });
	addEventListener('keydown', release);
	update();
	return () => {
		scroller.removeEventListener('scroll', onscroll);
		for (const type of ['wheel', 'touchmove', 'pointerdown'] as const)
			scroller.removeEventListener(type, release);
		removeEventListener('keydown', release);
		pinned = false;
		cancelAnimationFrame(frame);
	};
}
