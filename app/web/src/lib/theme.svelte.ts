import { THEME_COOKIE, THEME_MAX_AGE, type Theme } from './theme.ts';

const DARK = '(prefers-color-scheme: dark)';

/**
 * Whether the page is dark right now, for what CSS can't reach: canvas colors and the run
 * palette. It mirrors the `dark` class on <html>, whoever sets it (the server, app.html's
 * script when the system changes, or `setTheme`). Always light on the server.
 */
class Scheme {
	dark = $state(false);

	constructor() {
		if (typeof document === 'undefined') return;
		const root = document.documentElement;
		const read = () => (this.dark = root.classList.contains('dark'));
		read();
		new MutationObserver(read).observe(root, { attributes: true, attributeFilter: ['class'] });
	}
}

export const scheme = new Scheme();

/** Applies an appearance at once and remembers it for the next page the server renders. */
export function setTheme(theme: Theme) {
	const root = document.documentElement;
	root.dataset.theme = theme;
	root.classList.toggle(
		'dark',
		theme === 'dark' || (theme === 'system' && matchMedia(DARK).matches)
	);
	document.cookie = `${THEME_COOKIE}=${theme}; path=/; max-age=${THEME_MAX_AGE}; samesite=lax`;
}
