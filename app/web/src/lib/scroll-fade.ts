import type { Attachment } from 'svelte/attachments';

/**
 * Marks a sideways scroller with `data-more` while content runs on past its right edge, so
 * `SCROLL_FADE` fades that edge: more columns that way, as the runs table says with its fade.
 */
export const scrollFade: Attachment<HTMLElement> = (el) => {
	const update = () => {
		const more = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
		el.toggleAttribute('data-more', more);
	};
	update();
	el.addEventListener('scroll', update, { passive: true });
	const resized = new ResizeObserver(update);
	resized.observe(el);
	if (el.firstElementChild) resized.observe(el.firstElementChild);
	return () => {
		el.removeEventListener('scroll', update);
		resized.disconnect();
	};
};

/** The fade `scrollFade` turns on: the last 2.5rem of the scroller's right edge. */
export const SCROLL_FADE =
	'data-more:[mask-image:linear-gradient(to_left,transparent,black_2.5rem)]';
