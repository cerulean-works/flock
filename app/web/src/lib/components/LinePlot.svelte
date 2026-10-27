<script lang="ts">
	import {
		computeDomain,
		drawBase,
		drawOverlay,
		prepare,
		stepFromX,
		yToPx,
		type PlotSeries
	} from '#lib/chart/engine.ts';
	import { formatX, formatXTick, formatXValue } from '#lib/chart/axes.ts';
	import { placeTip, Readout, tipSide, type TipBox } from '#lib/chart/readout.ts';
	import { cancel, schedule } from '#lib/chart/scheduler.ts';
	import { pointAt, SNAP_PX, smoothed, snapStep } from '#lib/chart/series.ts';
	import { formatMetric, formatTick, sameItems } from '#lib/utils.ts';
	import {
		getWorkspace,
		type Cursor,
		type PanelView,
		type Surface,
		type XRange
	} from '#lib/workspace/workspace.svelte.ts';
	import PlotTooltip, { TIP_METRICS, TIP_WIDTH, type Tip, type TipRow } from './PlotTooltip.svelte';

	interface Props {
		panel: number;
		surface: Surface;
		/** False while the plot is covered (table view, or a full-screen panel on top). */
		active: boolean;
		/** Intersection root for on-screen detection. */
		root?: HTMLElement | null;
		/** The panel with unsaved settings, for the editor's preview; else as saved. */
		view?: PanelView;
	}

	let { panel, surface, active, root = null, view: preview }: Props = $props();

	/** A line: one run's curve of one of the panel's metrics. */
	type Line = PlotSeries & { metric: number };

	const ws = getWorkspace();
	// Canvas colors come from the theme tokens (read once the plot is in the document).
	let palette = $state({ ink: '#172a50', paper: '#ffffff', wash: '#cce7fb' });

	const view = $derived(preview ?? ws.panels[panel]);
	const config = $derived(view.config);
	const log = $derived(config.log ?? false);
	/** The x axis; curves are put on it (see #lib/chart/axes.ts) as they're read. */
	const axis = $derived(ws.axisOf(view));
	const zoom = $derived(ws.zoomOf(panel, axis.id));
	const smoothing = $derived(config.smoothing ?? ws.smoothing);
	const showOriginal = $derived(config.showOriginal ?? ws.showOriginal);
	const ignoreOutliers = $derived(config.ignoreOutliers ?? ws.ignoreOutliers);

	// Rebuilt whenever live points arrive for the panel's metrics (its version changes), which
	// also gives the base layer new inputs to repaint with.
	const series = $derived.by((): Line[] => {
		void ws.version(view);
		const out: Line[] = [];
		for (const row of ws.visible) {
			view.metrics.forEach((metric, k) => {
				const s = ws.lineSeries(row, metric, axis);
				if (!s?.n) return;
				const color = ws.lineColor(view, row, k);
				out.push({ row, metric, color, series: s, values: smoothed(s, smoothing) });
			});
		}
		return out;
	});

	// Plot size in CSS pixels, measured after mount (0 during SSR).
	let area = $state<HTMLDivElement>();
	let base = $state<HTMLCanvasElement>();
	let overlay = $state<HTMLCanvasElement>();
	let w = $state(0);
	let h = $state(0);
	let dpr = $state(1);
	let onScreen = $state(false);

	// Grid cards use fixed tick counts so server and client render identical axes; the larger
	// plots scale their ticks with their measured size.
	const tickCounts = $derived(
		surface !== 'grid' && w && h
			? {
					xTicks: Math.min(12, Math.max(4, Math.round(w / 110))),
					yTicks: Math.min(8, Math.max(4, Math.round(h / 80)))
				}
			: { xTicks: 5, yTicks: 4 }
	);
	/**
	 * The x range shown: the viewer's drag-zoom, else the panel's set range (an unset end
	 * follows the data) if it was set on this axis, else null for the data's own.
	 */
	const xRange = $derived.by((): XRange | null => {
		if (zoom) return zoom;
		const { xMin, xMax } = config;
		if (xMin == null && xMax == null) return null;
		const on = config.xRangeAxis ?? (view.stream === 'system' ? '_runtime' : '_step');
		if (on !== axis.id) return null;
		let lo = Infinity;
		let hi = -Infinity;
		for (const { series: s } of series) {
			if (!s.n) continue;
			lo = Math.min(lo, s.firstStep);
			hi = Math.max(hi, s.lastStep);
		}
		if (!Number.isFinite(lo)) [lo, hi] = [0, 1];
		const a = xMin ?? Math.min(lo, xMax! - 1);
		const b = xMax ?? Math.max(hi, a + 1);
		return [a, b > a ? b : a + 1];
	});
	const yRange = $derived(
		config.yMin != null || config.yMax != null
			? ([config.yMin ?? null, config.yMax ?? null] as const)
			: null
	);
	const domain = $derived(
		computeDomain(series, {
			zoom: xRange,
			log,
			xKind: axis.kind,
			ignoreOutliers,
			yRange,
			...tickCounts
		})
	);

	// Axis titles: what the panel sets, else the x axis' name (`Step`, `train/global_step`...),
	// which W&B shows under every chart's x ticks.
	const xLabel = $derived(config.xLabel ?? axis.label);
	const yLabel = $derived(config.yLabel ?? null);
	/** Plot area insets in CSS pixels: the y tick gutter, and the x tick band and title below. */
	const gutter = $derived((surface === 'grid' ? 46 : 64) + (yLabel ? 16 : 0));
	const below = $derived(surface === 'grid' ? 36 : 38);
	const PLOT_TOP = 6;

	const pct = (v: number, lo: number, hi: number) => ((v - lo) / (hi - lo)) * 100;
	const yTicks = $derived(
		domain.yTicks.values
			.filter((t) => t >= domain.y0 && t <= domain.y1)
			.map((t) => ({
				t,
				top: 100 - pct(t, domain.y0, domain.y1),
				label: log
					? formatTick(10 ** t, 10 ** t, view.format)
					: formatTick(t, domain.yTicks.step, view.format)
			}))
	);
	const xTicks = $derived(
		domain.xTicks.values
			.filter((t) => t >= domain.x0 && t <= domain.x1)
			.map((t) => ({
				t,
				left: pct(t, domain.x0, domain.x1),
				label: formatXTick(axis, t, domain.xTicks.step)
			}))
	);

	// ------------------------------------------------------------------------------------------
	// Canvas lifecycle

	function observe(el: HTMLDivElement) {
		const css = getComputedStyle(el);
		palette = {
			ink: css.getPropertyValue('--foreground').trim() || palette.ink,
			paper: css.getPropertyValue('--background').trim() || palette.paper,
			wash: css.getPropertyValue('--selection').trim() || palette.wash
		};
		const ro = new ResizeObserver(([entry]) => {
			w = entry.contentRect.width;
			h = entry.contentRect.height;
			dpr = Math.min(window.devicePixelRatio || 1, 2);
		});
		ro.observe(el);
		const io = new IntersectionObserver(([entry]) => (onScreen = entry.isIntersecting), {
			root,
			rootMargin: '200px 0px'
		});
		io.observe(el);
		return () => {
			ro.disconnect();
			io.disconnect();
		};
	}

	const lineWidth = $derived(config.lineWidth ?? (surface === 'grid' ? 1.25 : 1.5));

	const key = {};
	/** Inputs of the last painted base layer; identical inputs never repaint. */
	let painted: unknown[] = [];
	$effect(() => {
		const inputs = [series, domain, w, h, dpr, smoothing > 0, showOriginal, lineWidth];
		const priority = active && onScreen ? 'urgent' : 'idle';
		if (!base || !w || !h || sameItems(inputs, painted)) return;
		const canvas = base;
		const opts = { smoothing: smoothing > 0, showOriginal, zoomed: !!xRange, lineWidth };
		const s = series;
		const d = domain;
		const [cw, ch, cdpr] = [w, h, dpr];
		schedule(
			key,
			() => {
				const surf = prepare(canvas, cw, ch, cdpr);
				if (surf) drawBase(surf, s, d, opts);
				painted = inputs;
			},
			priority
		);
	});

	$effect(() => () => cancel(key));

	// ------------------------------------------------------------------------------------------
	// Pointer: synced crosshair, nearest-line focus, drag to zoom, click to pin.

	let dragFrom = $state<number | null>(null);
	let dragTo = $state<number | null>(null);
	let ownsHover = false;

	/**
	 * The cursor as this plot follows it. On the hovered plot's x axis, at its x. On another,
	 * through the history row under the pointer (both plots of logged metrics): each run at its
	 * own x here (`xs`, by page row), the crosshair at the hovered run's (else the first run's).
	 * System metrics, sampled on a timer, only follow plots on their own axis.
	 */
	const cursor = $derived.by((): (Cursor & { xs: Float64Array | null }) | null => {
		const c = ws.cursor;
		if (!c) return null;
		if (c.axis === axis.id) return { ...c, xs: null };
		if (!c.rows || view.stream !== 'history') return null;
		const rows = c.rows;
		const xs = new Float64Array(rows.length).fill(NaN);
		let first = NaN;
		for (const row of ws.visible) {
			if (row >= rows.length || Number.isNaN(rows[row])) continue;
			const x = ws.xAtHistoryStep(row, axis, rows[row]);
			xs[row] = x;
			if (Number.isNaN(first)) first = x;
		}
		const at = c.nearest != null && c.nearest < xs.length ? xs[c.nearest] : NaN;
		const step = Number.isNaN(at) ? first : at;
		return Number.isNaN(step) ? null : { ...c, step, xs };
	});
	const here = $derived(cursor?.panel === panel && cursor.surface === surface);

	/** How far from the cursor (in x units) a point reads out: a few pixels of this plot. */
	const tol = $derived(w ? (SNAP_PX * (domain.x1 - domain.x0)) / w : 0);

	/**
	 * Lines drawn over the others: the focused run's, or only the line under the pointer when
	 * it is in this plot (one run's eight GPUs are told apart that way).
	 */
	const focused = $derived.by((): Line[] => {
		const f = ws.focus;
		if (f == null) return [];
		const mine = series.filter((s) => s.row === f);
		if (here && cursor!.nearest === f && cursor!.line != null) {
			const hit = mine.find((s) => s.metric === cursor!.line);
			if (hit) return [hit];
		}
		return mine;
	});

	/**
	 * Every line's point at the cursor, wherever it is: in this plot, or in another on the same
	 * x axis. A lookup per line into buffers kept across frames, so following the cursor never
	 * retraces the lines, and allocates little.
	 */
	const readout = new Readout();

	/**
	 * This plot's tooltip: its lines' values at the crosshair, in every plot on the hovered
	 * plot's x axis (as on W&B), not only the hovered one. Set as the overlay is drawn.
	 */
	let tip = $state.raw<Tip | null>(null);
	// None while something is dragged over the plots, nor in grid cards behind the editor.
	const tips = $derived(!ws.dragging && !(surface === 'grid' && ws.editing != null));

	/**
	 * The tooltip for the readings just taken: rows sorted high to low, windowed around the
	 * focused line, beside the crosshair, as many rows as fit in the plot. The hovered plot's
	 * (`float`) floats over the page, so its card doesn't clip it and it can sit beside the
	 * cursor outside the plot, rather than over the lines being hovered. The other plots following
	 * the cursor keep theirs inside the plot, so none spills over the plot the pointer is in.
	 */
	function layoutTip(
		lines: readonly Line[],
		step: number,
		fy: number,
		fx: number,
		d: typeof domain,
		[cw, ch]: [number, number],
		float: boolean
	): Tip | null {
		const n = readout.count;
		if (!n) return null;
		const x = ((step - d.x0) / (d.x1 - d.x0)) * cw;
		if (!(x >= 0 && x <= cw)) return null;
		const size = surface === 'grid' ? 'compact' : 'roomy';
		const order = readout.order;
		let at = -1;
		for (let r = 0; r < n; r++)
			if (readout.focused[order[r]]) {
				at = r;
				break;
			}
		// In the grid, every plot's tooltip sits alike: on the side the hovered pointer decides,
		// top-aligned with the plot area. Full screen, it follows the pointer.
		const side = surface === 'grid' ? tipSide(fx) : null;
		// The box, and the crosshair's point (or the plot area's top), in its coordinates: the
		// window's, or the plot's.
		const plot = {
			x: gutter + x,
			y: PLOT_TOP + (side ? 0 : fy * ch),
			w: gutter + cw + 6,
			h: PLOT_TOP + ch + below
		};
		let box: TipBox | null = null;
		if (float && area) {
			const rect = area.getBoundingClientRect();
			box = {
				x: rect.left + x,
				y: rect.top + (side ? 0 : fy * ch),
				w: window.innerWidth,
				h: window.innerHeight
			};
		}
		const { fit, width, ...pos } = placeTip(n, at, plot, TIP_METRICS[size], TIP_WIDTH[size], {
			float: box,
			side
		});
		const fmt = view.format;
		const raw = smoothing > 0;
		const byMetric = ws.byMetric(view);
		const several = view.metrics.length > 1;
		const rows: TipRow[] = [];
		for (let r = fit.start; r < fit.start + fit.rows; r++) {
			const k = order[r];
			const line = lines[readout.line[k]];
			const label = ws.meta.metrics[line.metric].label;
			const xAt = readout.step[k];
			rows.push({
				key: `${line.row}:${line.metric}`,
				color: line.color,
				value: formatMetric(readout.value[k], fmt),
				raw: raw ? formatMetric(line.series.y[readout.point[k]], fmt) : null,
				name: byMetric ? label : ws.page.runs[line.row].name,
				metric: !byMetric && several ? label : null,
				step: xAt !== step ? formatXValue(axis, xAt) : null,
				focused: readout.focused[k] === 1
			});
		}
		return {
			...pos,
			width,
			header: formatX(axis, step),
			rows,
			above: fit.above,
			below: fit.below,
			float
		};
	}

	const overlayKey = {};
	$effect(() => {
		const ready = overlay && w && h && active && onScreen;
		if (!ready) {
			// Covered or scrolled away: no tooltip, nor a pending one to come back.
			cancel(overlayKey);
			tip = null;
			return;
		}
		const canvas = overlay!;
		const size: [number, number] = [w, h];
		const cdpr = dpr;
		const d = domain;
		const c = cursor;
		const lines = series;
		const focus = focused;
		const within = tol;
		const withTip = tips;
		const float = here;
		const opts = {
			focus,
			markers: readout,
			crosshair: c?.step ?? null,
			selection:
				dragFrom != null && dragTo != null ? ([dragFrom, dragTo] as [number, number]) : null,
			zoomed: !!xRange,
			dim: ws.focus != null,
			...palette
		};
		// In the next frame, as one draw however many moves came before it: hovering one plot
		// redraws every plot on its x axis, and lays out its tooltip.
		schedule(
			overlayKey,
			() => {
				const xs = c?.xs;
				if (c) readout.read(lines, xs ? (j) => xs[lines[j].row] : c.step, within, focus);
				else readout.clear();
				const surf = prepare(canvas, size[0], size[1], cdpr);
				if (surf) drawOverlay(surf, d, opts);
				tip = withTip && c ? layoutTip(lines, c.step, c.fy, c.fx, d, size, float) : null;
			},
			'urgent'
		);
	});

	$effect(() => () => cancel(overlayKey));

	function local(e: PointerEvent | MouseEvent) {
		const rect = area!.getBoundingClientRect();
		return {
			x: Math.max(0, Math.min(rect.width, e.clientX - rect.left)),
			y: e.clientY - rect.top
		};
	}

	/** The line nearest the pointer, within a few dozen pixels of it, with a point at `step`. */
	function nearestLine(step: number, y: number): Line | null {
		let best: Line | null = null;
		let bestDist = surface === 'grid' ? 40 : 60;
		for (const s of series) {
			const i = pointAt(s.series, step, tol);
			if (i < 0) continue;
			const v = s.values[i];
			if (!Number.isFinite(v)) continue;
			const dist = Math.abs(yToPx(domain, h, v) - y);
			if (dist < bestDist) {
				bestDist = dist;
				best = s;
			}
		}
		return best;
	}

	function onpointermove(e: PointerEvent) {
		// Something is being moved or resized over the plots: no crosshair until it lands.
		if (!area || !w || ws.dragging) return;
		const { x, y } = local(e);
		if (dragFrom != null) dragTo = x;
		// Read out where a line logged a point, as W&B does: sparse lines (an eval every few
		// hundred steps, a metric logged once) have no point under most of the plot.
		const raw = stepFromX(domain, w, x);
		const snapped = snapStep(
			series.map((s) => s.series),
			raw
		);
		const step = snapped >= domain.x0 && snapped <= domain.x1 ? snapped : raw;
		const line = nearestLine(step, y);
		const nearest = line?.row ?? null;
		// The history row under the pointer, per run, for plots on other x axes to follow.
		let rows: Float64Array | null = null;
		if (view.stream === 'history') {
			rows = new Float64Array(ws.page.runs.length).fill(NaN);
			for (const row of ws.visible) rows[row] = ws.historyStepAt(row, axis, step);
		}
		ws.cursor = {
			step,
			tol,
			rows,
			axis: axis.id,
			panel,
			view,
			surface,
			fy: Math.max(0, Math.min(1, y / h)),
			fx: Math.max(0, Math.min(1, x / w)),
			nearest,
			line: line?.metric ?? null
		};
		if (ws.hover !== nearest) ws.hover = nearest;
		ownsHover = true;
	}

	function onpointerleave() {
		if (dragFrom != null) return;
		ws.cursor = null;
		if (ownsHover) ws.hover = null;
		ownsHover = false;
	}

	function onpointerdown(e: PointerEvent) {
		if (e.button !== 0 || !area) return;
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		dragFrom = dragTo = local(e).x;
	}

	function onpointerup(e: PointerEvent) {
		if (dragFrom == null) return;
		const from = dragFrom;
		const to = local(e).x;
		dragFrom = dragTo = null;
		if (Math.abs(to - from) > 5) {
			const a = stepFromX(domain, w, Math.min(from, to));
			const b = stepFromX(domain, w, Math.max(from, to));
			if (b > a) ws.setZoom(panel, [a, b], axis.id);
		} else if (ws.cursor?.nearest != null) {
			ws.togglePin(ws.cursor.nearest);
		}
		const { y } = local(e);
		if (y < 0 || y > h) onpointerleave();
	}

	function onpointercancel() {
		dragFrom = dragTo = null;
	}

	function ondblclick() {
		ws.setZoom(panel, null);
	}

	/** Snapshot of both layers on a paper background, for "Download PNG". */
	export function snapshot(): HTMLCanvasElement | null {
		if (!base || !overlay) return null;
		const out = document.createElement('canvas');
		out.width = base.width;
		out.height = base.height;
		const ctx = out.getContext('2d')!;
		ctx.fillStyle = palette.paper;
		ctx.fillRect(0, 0, out.width, out.height);
		ctx.drawImage(base, 0, 0);
		ctx.drawImage(overlay, 0, 0);
		return out;
	}

	// Lines are grouped by run, so runs are counted where the row changes.
	const runs = $derived(series.filter((s, i) => i === 0 || series[i - 1].row !== s.row).length);
</script>

<!-- Axes are DOM positioned in percent of the plot area, so they render on the server. Axis
     titles widen the gutter (y) and deepen the tick band (x) they sit in. -->
<div
	class={[
		'plot relative h-full min-h-0 text-muted-foreground tabular-nums select-none',
		surface === 'grid' ? 'text-[11px]' : 'text-[12.5px]'
	]}
	style:--gutter="{gutter}px"
	style:--below="{below}px"
>
	{#if yLabel}
		<span
			class="absolute top-[calc(50%_-_var(--below)/2)] left-2 max-w-48 -translate-x-1/2 -translate-y-1/2 -rotate-90 truncate whitespace-nowrap text-foreground/80"
			title={yLabel}>{yLabel}</span
		>
	{/if}
	<div
		class="absolute top-1.5 bottom-(--below) left-0 w-[calc(var(--gutter)-8px)]"
		aria-hidden="true"
	>
		{#each yTicks as tick (tick.t)}
			<span class="absolute right-0 -translate-y-1/2 whitespace-nowrap" style:top="{tick.top}%"
				>{tick.label}</span
			>
		{/each}
	</div>
	<div
		class="area absolute top-1.5 right-1.5 bottom-(--below) left-(--gutter)"
		bind:this={area}
		{@attach observe}
	>
		{#each yTicks as tick (tick.t)}
			<div class="absolute inset-x-0 h-px bg-divider" style:top="{tick.top}%"></div>
		{/each}
		<canvas
			class={[
				'absolute inset-0 size-full transition-opacity duration-100 ease-out',
				active && ws.focus != null && 'opacity-16'
			]}
			bind:this={base}
		></canvas>
		<canvas
			class="absolute inset-0 size-full cursor-crosshair touch-none"
			bind:this={overlay}
			{onpointermove}
			{onpointerleave}
			{onpointerdown}
			{onpointerup}
			{onpointercancel}
			{ondblclick}
			aria-label="{view.title} line chart, {runs} {runs === 1 ? 'run' : 'runs'}"
		></canvas>
		{#if !series.length}
			<div
				class="pointer-events-none absolute inset-0 grid place-items-center text-[13px] text-faint"
			>
				No runs plotted
			</div>
		{/if}
	</div>
	<div
		class="absolute right-1.5 left-(--gutter) h-[18px]"
		style:bottom="{surface === 'grid' ? 14 : 16}px"
		aria-hidden="true"
	>
		{#each xTicks as tick (tick.t)}
			<span class="absolute -translate-x-1/2 whitespace-nowrap" style:left="{tick.left}%"
				>{tick.label}</span
			>
		{/each}
	</div>
	<!-- The x axis' name, at its right end under the ticks, as W&B puts it. -->
	<span
		class={[
			'absolute right-1.5 bottom-0 left-(--gutter) truncate text-right',
			surface === 'grid' ? 'text-[10.5px] leading-[14px]' : 'text-[11.5px] leading-4',
			config.xLabel ? 'text-foreground/80' : 'text-faint'
		]}
		title={xLabel}>{xLabel}</span
	>
	{#if tip}
		<!-- Remounted when it starts or stops floating: it moves between the document's end and
		     here. -->
		{#key tip.float}
			<PlotTooltip {tip} compact={surface === 'grid'} />
		{/key}
	{/if}
</div>
