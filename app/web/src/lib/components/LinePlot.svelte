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
	import { formatXTick } from '#lib/chart/axes.ts';
	import { cancel, schedule } from '#lib/chart/scheduler.ts';
	import { smoothed } from '#lib/chart/series.ts';
	import { formatTick, sameItems } from '#lib/utils.ts';
	import {
		getWorkspace,
		type PanelView,
		type Surface,
		type XRange
	} from '#lib/workspace/workspace.svelte.ts';

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
	let palette = $state({ ink: '#172a50', paper: '#fcfbf6', wash: '#cce7fb' });

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

	// Axis titles: what the panel sets; the larger plots name the x axis anyway, as W&B does.
	const xLabel = $derived(config.xLabel ?? (surface === 'grid' ? null : axis.label));
	const yLabel = $derived(config.yLabel ?? null);

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

	// Steps, times and metrics don't line up, so the crosshair only follows panels on the same
	// x axis.
	const cursor = $derived(ws.cursor && ws.cursor.axis === axis.id ? ws.cursor : null);
	const here = $derived(cursor?.panel === panel && cursor.surface === surface);

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

	const markers = $derived(
		focused.map((line) => {
			if (!cursor || !line.series.covers(cursor.step)) return null;
			const i = line.series.indexNear(cursor.step);
			return { step: line.series.stepAt(i), value: line.values[i] };
		})
	);

	$effect(() => {
		const ready = overlay && w && h && active && onScreen;
		if (!ready) return;
		const surf = prepare(overlay!, w, h, dpr);
		if (!surf) return;
		const crosshair = cursor ? (markers.find((m) => m)?.step ?? cursor.step) : null;
		const selection: [number, number] | null =
			dragFrom != null && dragTo != null ? [dragFrom, dragTo] : null;
		const lines = focused.length ? focused : [null];
		// One pass per focused line; the crosshair and selection are drawn once, by the first.
		lines.forEach((focus, i) =>
			drawOverlay(surf, domain, {
				focus,
				marker: markers[i] ?? null,
				crosshair: i ? null : crosshair,
				selection: i ? null : selection,
				zoomed: !!xRange,
				...palette
			})
		);
	});

	function local(e: PointerEvent | MouseEvent) {
		const rect = area!.getBoundingClientRect();
		return {
			x: Math.max(0, Math.min(rect.width, e.clientX - rect.left)),
			y: e.clientY - rect.top
		};
	}

	function nearestLine(step: number, y: number): Line | null {
		let best: Line | null = null;
		let bestDist = surface === 'grid' ? 40 : 60;
		for (const s of series) {
			if (!s.series.covers(step)) continue;
			const v = s.values[s.series.indexNear(step)];
			if (Number.isNaN(v)) continue;
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
		const step = stepFromX(domain, w, x);
		const line = nearestLine(step, y);
		const nearest = line?.row ?? null;
		ws.cursor = {
			step,
			axis: axis.id,
			panel,
			view,
			surface,
			clientX: e.clientX,
			clientY: e.clientY,
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
	style:--gutter="{(surface === 'grid' ? 46 : 64) + (yLabel ? 16 : 0)}px"
	style:--below="{xLabel ? 38 : 22}px"
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
		style:bottom="{xLabel ? 16 : 0}px"
		aria-hidden="true"
	>
		{#each xTicks as tick (tick.t)}
			<span class="absolute -translate-x-1/2 whitespace-nowrap" style:left="{tick.left}%"
				>{tick.label}</span
			>
		{/each}
	</div>
	{#if xLabel}
		<span
			class="absolute right-1.5 bottom-0 left-(--gutter) truncate text-center leading-4 text-foreground/80"
			title={xLabel}>{xLabel}</span
		>
	{/if}
</div>
