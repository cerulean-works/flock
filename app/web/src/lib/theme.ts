/**
 * The appearance someone picked on their account page, kept in a cookie so the server renders
 * the right one from the first byte. `system` follows the operating system: the server can't
 * know which that is, so the inline script in app.html resolves it before the body paints.
 */
export type Theme = 'light' | 'dark' | 'system';

export const THEMES: readonly Theme[] = ['light', 'dark', 'system'];

export const THEME_COOKIE = 'flock-theme';

/** A year; the choice is renewed each time it's made. */
export const THEME_MAX_AGE = 60 * 60 * 24 * 365;

export const parseTheme = (value: string | null | undefined): Theme =>
	value === 'light' || value === 'dark' ? value : 'system';
