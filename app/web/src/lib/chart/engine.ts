import type { AxisKind } from './axes.ts';
import { logTicks, niceDomain, niceTicks, timeTicks, wallTicks, type Ticks } from './scale.ts';
import type { Series } from './series.ts';

export interface Domain {
	x0: number;
	x1: number;
	/** In log10 units when `log` is set. */
	y0: number;
	y1: number;
	log: boolean;
	xTicks: Ticks;
	yTicks: Ticks;
}

export interface PlotSeries {
	row: number;
	color: string;
	series: Series;
	/** Values to draw (smoothed when smoothing is on), aligned with `series.x`. */
	values: Float32Array;
}

export interface DomainOptions {
	zoom: readonly [number, number] | null;
	log: boolean;
	ignoreOutliers: boolean;
	/** What the x values are: durations and wall times get ticks on round times. */
	xKind?: AxisKind;
	/** Approximate tick counts. */
	xTicks?: number;
	yTicks?: number;
	/** Fixed y bounds set on the panel, in data units (not log10); null leaves that end automatic. */
	yRange?: readonly [number | null, number | null] | null;
}

/** Share of the x span left empty on each side of an unzoomed plot, so end points clear the frame. */
const X_PAD = 0.02;

/** Visible index window of a series for an x range, padded by one point on each side. */
function window(s: Series, x0: number, x1: number, zoomed: boolean) {
	if (!zoomed) return [0, s.n - 1];
	return [Math.max(0, s.indexNear(x0) - 1), Math.min(s.n - 1, s.indexNear(x1) + 1)];
}

/** Indices of the points with steps inside [x0, x1]; empty when the first exceeds the second. */
function inside(s: Series, x0: number, x1: number): [number, number] {
	let i0 = s.indexNear(x0);
	if (s.stepAt(i0) < x0) i0++;
	let i1 = s.indexNear(x1);
	if (s.stepAt(i1) > x1) i1--;
	return [i0, i1];
}

export function computeDomain(series: PlotSeries[], opts: DomainOptions): Domain {
	let x0 = Infinity;
	let x1 = -Infinity;
	if (opts.zoom) {
		[x0, x1] = opts.zoom;
		if (!(x1 > x0)) x1 = x0 + 1;
	} else {
		for (const { series: s } of series) {
			if (!s.n) continue;
			x0 = Math.min(x0, s.firstStep);
			x1 = Math.max(x1, s.lastStep);
		}
		if (!Number.isFinite(x0)) [x0, x1] = [0, 1];
		// Like W&B, an axis whose data starts close to zero starts at zero.
		if (x0 > 0 && x0 < (x1 - x0) * 0.1) x0 = 0;
		if (!(x1 > x0)) [x0, x1] = [x0 - 0.5, x0 + 0.5];
		const pad = (x1 - x0) * X_PAD;
		x0 -= pad;
		x1 += pad;
	}

	let lo = Infinity;
	let hi = -Infinity;
	const fast = !opts.zoom && !opts.log && !opts.ignoreOutliers;
	if (fast) {
		// Series keep their extents up to date as points arrive; smoothed values stay within them.
		for (const { series: s } of series) {
			if (s.min < lo) lo = s.min;
			if (s.max > hi) hi = s.max;
		}
	} else {
		const sample: number[] = [];
		const add = (v: number) => {
			if (!Number.isFinite(v)) return;
			if (opts.ignoreOutliers) sample.push(v);
			else {
				if (v < lo) lo = v;
				if (v > hi) hi = v;
			}
		};
		for (const { series: s, values } of series) {
			if (!s.n) continue;
			const [i0, i1] = opts.zoom ? inside(s, x0, x1) : [0, s.n - 1];
			const raw = s.y;
			// Fit what is drawn: the smoothed line and the faded original.
			for (const ys of values.buffer === raw.buffer ? [raw] : [raw, values]) {
				const n = Math.min(s.n, ys.length);
				const at = (i: number) => (opts.log ? (ys[i] > 0 ? Math.log10(ys[i]) : NaN) : ys[i]);
				for (let i = Math.max(0, i0); i <= Math.min(i1, n - 1); i++) add(at(i));
				if (!opts.zoom) continue;
				// Where the line crosses the frame edges, between point `a` and the next.
				const cross = (a: number, x: number) => {
					if (a < 0 || a + 1 >= n) return;
					const xa = s.stepAt(a);
					const va = at(a);
					add(va + (at(a + 1) - va) * ((x - xa) / (s.stepAt(a + 1) - xa)));
				};
				cross(i0 - 1, x0);
				cross(i1, x1);
			}
		}
		if (opts.ignoreOutliers && sample.length) {
			const sorted = Float64Array.from(sample).sort();
			const at = (p: number) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
			lo = at(0.02);
			hi = at(0.98);
		}
	}
	if (!Number.isFinite(lo)) [lo, hi] = opts.log ? [-1, 0] : [0, 1];

	// Fixed ends are used exactly; the automatic end is padded against the full span.
	const fixed = (v: number | null | undefined) =>
		v == null || !Number.isFinite(v) ? null : opts.log ? (v > 0 ? Math.log10(v) : null) : v;
	const fixLo = fixed(opts.yRange?.[0]);
	const fixHi = fixed(opts.yRange?.[1]);
	let [y0, y1] = niceDomain(fixLo ?? lo, fixHi ?? hi);
	if (fixLo != null) y0 = fixLo;
	if (fixHi != null) y1 = fixHi;
	if (!(y1 > y0)) {
		const d = Math.abs(fixLo ?? fixHi ?? 0) * 0.1 || 1;
		if (fixLo != null && fixHi == null) y1 = y0 + d;
		else if (fixHi != null && fixLo == null) y0 = y1 - d;
		else [y0, y1] = niceDomain(lo, hi);
	}
	return {
		x0,
		x1,
		y0,
		y1,
		log: opts.log,
		xTicks: (opts.xKind === 'duration' ? timeTicks : opts.xKind === 'time' ? wallTicks : niceTicks)(
			x0,
			x1,
			opts.xTicks ?? 5
		),
		yTicks: opts.log ? logTicks(y0, y1) : niceTicks(y0, y1, opts.yTicks ?? 4)
	};
}

export interface Surface {
	ctx: CanvasRenderingContext2D;
	/** CSS pixels. */
	w: number;
	h: number;
	dpr: number;
}

export function prepare(
	canvas: HTMLCanvasElement,
	w: number,
	h: number,
	dpr: number
): Surface | null {
	const W = Math.max(1, Math.round(w * dpr));
	const H = Math.max(1, Math.round(h * dpr));
	if (canvas.width !== W) canvas.width = W;
	if (canvas.height !== H) canvas.height = H;
	const ctx = canvas.getContext('2d');
	if (!ctx) return null;
	ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
	ctx.clearRect(0, 0, w, h);
	return { ctx, w, h, dpr };
}

/** Trace one series as a polyline. Points mapping to the same device pixel column collapse to min/max. */
function trace(
	{ ctx, w, h, dpr }: Surface,
	series: Series,
	values: Float32Array,
	d: Domain,
	zoomed: boolean
) {
	const n = Math.min(series.n, values.length);
	if (!n) return;
	const xs = series.x;
	const sx = w / (d.x1 - d.x0);
	const sy = h / (d.y1 - d.y0);
	const [i0, i1] = window(series, d.x0, d.x1, zoomed);

	if (n === 1) {
		const v = d.log ? Math.log10(values[0]) : values[0];
		if (!Number.isFinite(v)) return;
		const X = (xs[0] - d.x0) * sx;
		ctx.moveTo(X + 2, h - (v - d.y0) * sy);
		ctx.arc(X, h - (v - d.y0) * sy, 2, 0, Math.PI * 2);
		return;
	}

	let pen = false;
	let col = NaN;
	let colMin = 0;
	let colMax = 0;
	let lastY = 0;
	for (let i = i0; i <= i1; i++) {
		let v = values[i];
		if (d.log) v = v > 0 ? Math.log10(v) : NaN;
		if (!Number.isFinite(v)) {
			pen = false;
			continue;
		}
		const X = (xs[i] - d.x0) * sx;
		const Y = h - (v - d.y0) * sy;
		const c = Math.floor(X * dpr);
		if (pen && c === col) {
			if (Y < colMin) colMin = Y;
			if (Y > colMax) colMax = Y;
			lastY = Y;
			continue;
		}
		if (pen && colMax - colMin > 0.5) {
			const cx = col / dpr;
			ctx.lineTo(cx, colMin);
			ctx.lineTo(cx, colMax);
			ctx.lineTo(cx, lastY);
		}
		if (pen) ctx.lineTo(X, Y);
		else ctx.moveTo(X, Y);
		pen = true;
		col = c;
		colMin = colMax = lastY = Y;
	}
}

export interface BaseOptions {
	smoothing: boolean;
	showOriginal: boolean;
	zoomed: boolean;
	lineWidth: number;
}

/** All plotted runs at full strength. Redrawn only when data, domain or size changes. */
export function drawBase(s: Surface, series: PlotSeries[], d: Domain, o: BaseOptions) {
	const { ctx } = s;
	ctx.save();
	ctx.beginPath();
	ctx.rect(0, -2, s.w, s.h + 4);
	ctx.clip();
	ctx.lineJoin = 'bevel';
	ctx.lineCap = 'butt';
	if (o.smoothing && o.showOriginal) {
		ctx.globalAlpha = 0.18;
		ctx.lineWidth = 1;
		for (const p of series) {
			ctx.beginPath();
			trace(s, p.series, p.series.y, d, o.zoomed);
			ctx.strokeStyle = p.color;
			ctx.stroke();
		}
	}
	ctx.globalAlpha = 0.92;
	ctx.lineWidth = o.lineWidth;
	for (const p of series) {
		ctx.beginPath();
		trace(s, p.series, p.values, d, o.zoomed);
		ctx.strokeStyle = p.color;
		ctx.stroke();
	}
	ctx.restore();
}

export interface OverlayOptions {
	focus: PlotSeries | null;
	/** Point on the focused series to mark, in data units. */
	marker: { step: number; value: number } | null;
	crosshair: number | null;
	/** Drag-to-zoom selection in CSS pixels. */
	selection: readonly [number, number] | null;
	zoomed: boolean;
	ink: string;
	paper: string;
	wash: string;
}

/** Focus line, crosshair, marker and zoom selection. Cheap; redrawn on every pointer move. */
export function drawOverlay(s: Surface, d: Domain, o: OverlayOptions) {
	const { ctx, w, h } = s;
	const sx = w / (d.x1 - d.x0);
	const sy = h / (d.y1 - d.y0);

	if (o.selection) {
		const [a, b] = o.selection;
		ctx.fillStyle = o.wash;
		ctx.globalAlpha = 0.55;
		ctx.fillRect(Math.min(a, b), 0, Math.abs(b - a), h);
		ctx.globalAlpha = 1;
	}

	if (o.focus) {
		ctx.save();
		ctx.beginPath();
		ctx.rect(0, -2, w, h + 4);
		ctx.clip();
		ctx.lineJoin = 'round';
		ctx.lineCap = 'round';
		ctx.lineWidth = 2.25;
		ctx.strokeStyle = o.focus.color;
		ctx.beginPath();
		trace(s, o.focus.series, o.focus.values, d, o.zoomed);
		ctx.stroke();
		ctx.restore();
	}

	if (o.crosshair != null) {
		const x = Math.round((o.crosshair - d.x0) * sx) + 0.5;
		if (x >= 0 && x <= w) {
			ctx.strokeStyle = o.ink;
			ctx.globalAlpha = 0.45;
			ctx.lineWidth = 1;
			ctx.setLineDash([3, 3]);
			ctx.beginPath();
			ctx.moveTo(x, 0);
			ctx.lineTo(x, h);
			ctx.stroke();
			ctx.setLineDash([]);
			ctx.globalAlpha = 1;
		}
	}

	if (o.marker && o.focus) {
		const v = d.log ? Math.log10(o.marker.value) : o.marker.value;
		if (Number.isFinite(v)) {
			const x = (o.marker.step - d.x0) * sx;
			const y = h - (v - d.y0) * sy;
			ctx.beginPath();
			ctx.arc(x, y, 4, 0, Math.PI * 2);
			ctx.fillStyle = o.focus.color;
			ctx.fill();
			ctx.lineWidth = 2;
			ctx.strokeStyle = o.paper;
			ctx.stroke();
		}
	}
}

/** Data-space position of a CSS-pixel coordinate inside the plot. */
export const stepFromX = (d: Domain, w: number, x: number) => d.x0 + (x / w) * (d.x1 - d.x0);
export const yToPx = (d: Domain, h: number, v: number) => {
	const y = d.log ? Math.log10(v) : v;
	return h - ((y - d.y0) / (d.y1 - d.y0)) * h;
};
