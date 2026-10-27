import { pointAt, type Series } from './series.ts';

/** A line to read out: its points, the values drawn (smoothed or not) and its color. */
export interface ReadLine {
	series: Series;
	values: Float32Array;
	color: string;
}

/**
 * Every line's point at the cursor, for a plot's markers and tooltip. Read on every frame the
 * cursor moves, in every plot on its x axis (a hundred runs over a dozen panels), so the buffers
 * are kept and only grow: following the cursor allocates nothing per line.
 *
 * A line reads out at its point nearest the cursor (see `pointAt`) when that point's value is
 * finite; a missing or overflowed value (NaN, ±inf) is a gap in the line and reads out nothing.
 */
export class Readout {
	/** Readings taken by the last `read`. */
	count = 0;
	/** Per reading: the index of its line in the lines read, of its point, its step and value. */
	line = new Int32Array(16);
	point = new Int32Array(16);
	step = new Float64Array(16);
	value = new Float64Array(16);
	/** Per reading: its line's color, and 1 when the line is drawn over the others. */
	color: string[] = [];
	focused = new Uint8Array(16);
	/** Reading indices by value, high to low (ties keep line order). */
	order: number[] = [];

	/**
	 * Read every line at `at`: one x for all, or per line (by index) where each run sits at its
	 * own x, as when the cursor is in a panel on another axis. NaN reads nothing.
	 */
	read(
		lines: readonly ReadLine[],
		at: number | ((line: number) => number),
		tol: number,
		focus: readonly ReadLine[] = []
	): this {
		this.grow(lines.length);
		let k = 0;
		for (let j = 0; j < lines.length; j++) {
			const l = lines[j];
			const x = typeof at === 'number' ? at : at(j);
			if (Number.isNaN(x)) continue;
			const i = pointAt(l.series, x, tol);
			if (i < 0 || i >= l.values.length) continue;
			const v = l.values[i];
			if (!Number.isFinite(v)) continue;
			this.line[k] = j;
			this.point[k] = i;
			this.step[k] = l.series.stepAt(i);
			this.value[k] = v;
			this.color[k] = l.color;
			this.focused[k] = focus.length && focus.includes(l) ? 1 : 0;
			k++;
		}
		this.count = k;
		const order = this.order;
		order.length = k;
		for (let r = 0; r < k; r++) order[r] = r;
		const value = this.value;
		order.sort((a, b) => value[b] - value[a] || a - b);
		return this;
	}

	/** Forget the readings (keeping the buffers). */
	clear(): this {
		this.count = 0;
		this.order.length = 0;
		return this;
	}

	private grow(n: number) {
		if (n <= this.line.length) return;
		const cap = Math.max(n, this.line.length * 2);
		this.line = new Int32Array(cap);
		this.point = new Int32Array(cap);
		this.step = new Float64Array(cap);
		this.value = new Float64Array(cap);
		this.focused = new Uint8Array(cap);
	}
}

/** Sizes of a tooltip's parts, in CSS pixels. */
export interface TooltipMetrics {
	/** Header (the cursor's x) and padding around the rows. */
	chrome: number;
	row: number;
	/** A "N more above/below" line. */
	more: number;
}

/**
 * Which readings a tooltip lists when not all of them fit in `space` pixels: a window of the
 * sorted readings, centered on the focused one (`at`, or -1) and otherwise starting at the top,
 * with a line saying how many more there are above and below it.
 */
export function tooltipRows(total: number, at: number, space: number, m: TooltipMetrics) {
	const fit = Math.floor((space - m.chrome) / m.row);
	let rows = total;
	if (total > fit) {
		// Room for the "more" lines too: without the focused one in view, only one is needed.
		const lines = at <= 0 ? 1 : 2;
		rows = Math.max(1, Math.min(total, Math.floor((space - m.chrome - lines * m.more) / m.row)));
	}
	const start = at < 0 ? 0 : Math.max(0, Math.min(at - Math.floor(rows / 2), total - rows));
	const above = start;
	const below = Math.max(0, total - start - rows);
	const height = m.chrome + rows * m.row + (above ? m.more : 0) + (below ? m.more : 0);
	return { start, rows, above, below, height };
}

/**
 * Top-left corner of a `bw`×`bh` tooltip for a crosshair at `x` in a `w`×`h` box, beside the
 * crosshair (`gap` away) on the side with room for it, and vertically near `y`. It stays inside
 * the box (`pad` from its edges) whenever it fits.
 */
export function tooltipPosition(
	x: number,
	y: number,
	bw: number,
	bh: number,
	w: number,
	h: number,
	gap = 12,
	pad = 2
): { left: number; top: number } {
	let left = x + gap;
	if (left + bw > w - pad) left = x - gap - bw;
	if (left < pad) left = Math.max(pad, Math.min(x + gap, w - pad - bw));
	const top = Math.max(pad, Math.min(y - 12, h - pad - bh));
	return { left, top };
}

/** A point in a box: the crosshair's, in the box's coordinates, and the box's size. */
export interface TipBox {
	x: number;
	y: number;
	w: number;
	h: number;
}

/**
 * Where a tooltip of `total` readings goes and which of them it lists: sized for the `plot` it
 * reads out (as many rows as fit in it, at most `maxWidth` wide), and placed beside the
 * crosshair in the plot, or in `float` (the window) when it floats over the page. Floating
 * moves it, never grows it: a page of runs would otherwise make a column as tall as the window.
 */
export function placeTip(
	total: number,
	at: number,
	plot: TipBox,
	m: TooltipMetrics,
	maxWidth: number,
	float: TipBox | null = null
) {
	const fit = tooltipRows(total, at, plot.h - 4, m);
	const width = Math.min(maxWidth, plot.w - 4);
	const pos = float
		? tooltipPosition(float.x, float.y, width, fit.height, float.w, float.h, 16, 8)
		: tooltipPosition(plot.x, plot.y, width, fit.height, plot.w, plot.h);
	return { fit, width, ...pos };
}
