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
	import { cancel, schedule } from '#lib/chart/scheduler.ts';
	import { slotOf, smoothed } from '#lib/chart/series.ts';
	import { formatAxisStep, formatRuntime, formatTick, sameItems } from '#lib/utils.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	interface Props {
		metric: number;
		surface: 'grid' | 'fullscreen';
		/** False while the plot is covered (table view, or a full-screen panel on top). */
		active: boolean;
		/** Intersection root for on-screen detection. */
		root?: HTMLElement | null;
	}

	let { metric, surface, active, root = null }: Props = $props();

	const ws = getWorkspace();
	// Canvas colors come from the theme tokens (read once the plot is in the document).
	let palette = $state({ ink: '#172a50', paper: '#fcfbf6', wash: '#cce7fb' });

	const def = $derived(ws.meta.metrics[metric]);
	const log = $derived(ws.isLog(metric));
	const zoom = $derived(ws.zoomOf(metric));
	const smoothing = $derived(ws.smoothing);

	// Rebuilt whenever live points arrive for this metric (its version changes), which also
	// gives the base layer new inputs to repaint with.
	const series = $derived.by((): PlotSeries[] => {
		void ws.version(metric);
		const page = ws.page;
		const out: PlotSeries[] = [];
		for (const row of ws.visible) {
			const s = page.series[slotOf(page.metricCount, row, metric)];
			if (s) out.push({ row, color: ws.colors[row], series: s, values: smoothed(s, smoothing) });
		}
		return out;
	});
	const byRow = $derived(new Map(series.map((s) => [s.row, s])));

	// Plot size in CSS pixels, measured after mount (0 during SSR).
	let area = $state<HTMLDivElement>();
	let base = $state<HTMLCanvasElement>();
	let overlay = $state<HTMLCanvasElement>();
	let w = $state(0);
	let h = $state(0);
	let dpr = $state(1);
	let onScreen = $state(false);

	// Grid cards use fixed tick counts so server and client render identical axes; the full-screen
	// plot scales its ticks with its measured size.
	const tickCounts = $derived(
		surface === 'fullscreen' && w && h
			? {
					xTicks: Math.min(12, Math.max(4, Math.round(w / 110))),
					yTicks: Math.min(8, Math.max(4, Math.round(h / 80)))
				}
			: { xTicks: 5, yTicks: 4 }
	);
	const timeX = $derived(def.stream === 'system');
	const domain = $derived(
		computeDomain(series, { zoom, log, timeX, ignoreOutliers: ws.ignoreOutliers, ...tickCounts })
	);

	const pct = (v: number, lo: number, hi: number) => ((v - lo) / (hi - lo)) * 100;
	const yTicks = $derived(
		domain.yTicks.values
			.filter((t) => t >= domain.y0 && t <= domain.y1)
			.map((t) => ({
				t,
				top: 100 - pct(t, domain.y0, domain.y1),
				label: log
					? formatTick(10 ** t, 10 ** t, def.format)
					: formatTick(t, domain.yTicks.step, def.format)
			}))
	);
	const xTicks = $derived(
		domain.xTicks.values
			.filter((t) => t >= domain.x0 && t <= domain.x1)
			.map((t) => ({
				t,
				left: pct(t, domain.x0, domain.x1),
				label: timeX ? formatRuntime(t, domain.xTicks.step) : formatAxisStep(t)
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

	const key = {};
	/** Inputs of the last painted base layer; identical inputs never repaint. */
	let painted: unknown[] = [];
	$effect(() => {
		const inputs = [series, domain, w, h, dpr, smoothing > 0, ws.showOriginal, surface];
		const priority = active && onScreen ? 'urgent' : 'idle';
		if (!base || !w || !h || sameItems(inputs, painted)) return;
		const canvas = base;
		const opts = {
			smoothing: smoothing > 0,
			showOriginal: ws.showOriginal,
			zoomed: !!zoom,
			lineWidth: surface === 'fullscreen' ? 1.5 : 1.25
		};
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
	// Pointer: synced crosshair, nearest-run focus, drag to zoom, click to pin.

	let dragFrom = $state<number | null>(null);
	let dragTo = $state<number | null>(null);
	let ownsHover = false;

	// Steps and runtimes don't line up, so the crosshair only follows panels on the same x axis.
	const cursor = $derived(
		ws.cursor && ws.meta.metrics[ws.cursor.metric].stream === def.stream ? ws.cursor : null
	);
	const focus = $derived(ws.focus != null ? (byRow.get(ws.focus) ?? null) : null);

	const marker = $derived.by(() => {
		if (!cursor || !focus || !focus.series.covers(cursor.step)) return null;
		const i = focus.series.indexNear(cursor.step);
		return { step: focus.series.stepAt(i), value: focus.values[i] };
	});

	$effect(() => {
		const ready = overlay && w && h && active && onScreen;
		if (!ready) return;
		const surf = prepare(overlay!, w, h, dpr);
		if (!surf) return;
		drawOverlay(surf, domain, {
			focus,
			marker,
			crosshair: cursor ? (marker?.step ?? cursor.step) : null,
			selection: dragFrom != null && dragTo != null ? [dragFrom, dragTo] : null,
			zoomed: !!zoom,
			...palette
		});
	});

	function local(e: PointerEvent | MouseEvent) {
		const rect = area!.getBoundingClientRect();
		return {
			x: Math.max(0, Math.min(rect.width, e.clientX - rect.left)),
			y: e.clientY - rect.top
		};
	}

	function nearestRow(step: number, y: number): number | null {
		let best: number | null = null;
		let bestDist = surface === 'fullscreen' ? 60 : 40;
		for (const s of series) {
			if (!s.series.covers(step)) continue;
			const v = s.values[s.series.indexNear(step)];
			if (Number.isNaN(v)) continue;
			const dist = Math.abs(yToPx(domain, h, v) - y);
			if (dist < bestDist) {
				bestDist = dist;
				best = s.row;
			}
		}
		return best;
	}

	function onpointermove(e: PointerEvent) {
		if (!area || !w) return;
		const { x, y } = local(e);
		if (dragFrom != null) dragTo = x;
		const step = stepFromX(domain, w, x);
		const nearest = nearestRow(step, y);
		ws.cursor = { step, metric, surface, clientX: e.clientX, clientY: e.clientY, nearest };
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
			if (b - a >= 1) ws.setZoom(metric, [a, b]);
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
		ws.setZoom(metric, null);
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
</script>

<!-- Axes are DOM positioned in percent of the plot area, so they render on the server. -->
<div
	class={[
		'plot relative h-full min-h-0 text-muted-foreground tabular-nums select-none',
		surface === 'fullscreen' ? 'text-[12.5px] [--gutter:64px]' : 'text-[11px] [--gutter:46px]'
	]}
>
	<div class="absolute top-1.5 bottom-[22px] left-0 w-[calc(var(--gutter)-8px)]" aria-hidden="true">
		{#each yTicks as tick (tick.t)}
			<span class="absolute right-0 -translate-y-1/2 whitespace-nowrap" style:top="{tick.top}%"
				>{tick.label}</span
			>
		{/each}
	</div>
	<div
		class="area absolute top-1.5 right-1.5 bottom-[22px] left-(--gutter)"
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
			aria-label="{def.key} line chart, {series.length} runs"
		></canvas>
		{#if !series.length}
			<div
				class="pointer-events-none absolute inset-0 grid place-items-center text-[13px] text-faint"
			>
				No runs plotted
			</div>
		{/if}
	</div>
	<div class="absolute right-1.5 bottom-0 left-(--gutter) h-[18px]" aria-hidden="true">
		{#each xTicks as tick (tick.t)}
			<span class="absolute -translate-x-1/2 whitespace-nowrap" style:left="{tick.left}%"
				>{tick.label}</span
			>
		{/each}
	</div>
</div>
