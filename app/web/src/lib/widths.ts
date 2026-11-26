/**
 * Widths the viewer can drag: the app's sidebar, the runs table's name column, the group
 * editor's assistant and the eval items' columns. Kept in cookies, like the sidebar's open
 * state. The server renders the first three, so nothing jumps; the items are fetched in the
 * browser, which reads their widths there.
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
/** The runs table beside the charts, in the Visualize view (and the charts take the rest). */
export const TABLE_W: Width = { cookie: 'flock_table_w', min: 360, max: 1600, initial: 720 };
export const ASSISTANT_W: Width = {
	cookie: 'flock_assistant_w',
	min: 280,
	max: 640,
	initial: 320
};

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

/**
 * The eval items table's columns (027), one width for every run's so the runs stay side by side.
 * The initial widths fit two runs in Home's text column (70rem).
 */
export const ITEM_W = {
	doc: { cookie: 'flock_item_doc_w', min: 96, max: 360, initial: 128 },
	question: { cookie: 'flock_item_question_w', min: 200, max: 1400, initial: 360 },
	gold: { cookie: 'flock_item_gold_w', min: 120, max: 720, initial: 160 },
	run: { cookie: 'flock_item_run_w', min: 140, max: 720, initial: 200 }
} satisfies Record<string, Width>;
