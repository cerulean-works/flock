/**
 * Widths the viewer can drag: the app's sidebar and the runs table's name column. Kept in
 * cookies, like the sidebar's open state, so the server renders them and nothing jumps.
 */
export interface Width {
	cookie: string;
	min: number;
	max: number;
	/** The width before any dragging, and after a double-click on the handle. */
	initial: number;
}

export const SIDEBAR_W: Width = { cookie: 'flock_sidebar_w', min: 200, max: 420, initial: 256 };
export const NAME_W: Width = { cookie: 'flock_name_w', min: 220, max: 640, initial: 360 };

export const clampWidth = (w: Width, px: number) =>
	Math.round(Math.min(w.max, Math.max(w.min, px)));

/** A cookie's width, or the initial one when it's missing or not a number. */
export function readWidth(w: Width, value: string | undefined): number {
	const px = Number(value);
	return value && Number.isFinite(px) ? clampWidth(w, px) : w.initial;
}

export function saveWidth(w: Width, px: number) {
	document.cookie = `${w.cookie}=${clampWidth(w, px)}; path=/; max-age=31536000; samesite=lax`;
}
