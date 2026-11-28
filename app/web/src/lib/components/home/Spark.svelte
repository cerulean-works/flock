<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { Curve } from '#lib/home/curves.ts';
	import { formatNumber, formatStep } from '#lib/utils.ts';

	interface Props {
		curve: Curve;
		/** Draw the line in once (Home's arrival, H13). */
		draw?: boolean;
		/** Hover reads a point off the curve. */
		interactive?: boolean;
		/** The line's colour class (stroke-*). */
		tone?: string;
		/** Shade under the line. */
		area?: boolean;
		/** A dot on the latest point; `live` gives it a slow breathing ring. */
		dot?: boolean;
		live?: boolean;
		/** By the dot, in the room the flattening curve leaves: above it, or under it when the
		 * curve ends high. */
		end?: Snippet;
		class?: string;
	}

	let {
		curve,
		draw = false,
		interactive = false,
		tone = 'stroke-link',
		area = false,
		dot = false,
		live = false,
		end,
		class: className
	}: Props = $props();

	// Drawn in the box's own pixels: a stretched viewBox would need non-scaling strokes, which
	// break the draw-in's dash length in Chrome.
	let W = $state(100);
	let H = $state(40);
	const PAD = 2;

	const points = $derived.by(() => {
		const out: [number, number][] = [];
		for (let i = 0; i < curve.x.length; i++) {
			const y = curve.y[i];
			if (y != null && Number.isFinite(y)) out.push([curve.x[i], y]);
		}
		return out;
	});
	const box = $derived.by(() => {
		let x0 = Infinity,
			x1 = -Infinity,
			y0 = Infinity,
			y1 = -Infinity;
		for (const [x, y] of points) {
			x0 = Math.min(x0, x);
			x1 = Math.max(x1, x);
			y0 = Math.min(y0, y);
			y1 = Math.max(y1, y);
		}
		return { x0, x1: x1 === x0 ? x0 + 1 : x1, y0, y1: y1 === y0 ? y0 + 1 : y1 };
	});
	const sx = (x: number) => ((x - box.x0) / (box.x1 - box.x0)) * W;
	const sy = (y: number) => PAD + (1 - (y - box.y0) / (box.y1 - box.y0)) * (H - 2 * PAD);
	const d = $derived(
		points.map(([x, y], i) => `${i ? 'L' : 'M'}${sx(x).toFixed(2)},${sy(y).toFixed(2)}`).join('')
	);
	const fill = $derived(points.length ? `${d}L${W},${H}L0,${H}Z` : '');

	const last = $derived(points.at(-1) ?? null);
	const lastY = $derived(last ? sy(last[1]) : 0);
	/** Room above the end for its label; else it goes under. */
	const endAbove = $derived(lastY > 44);

	let at = $state<number | null>(null);
	function onpointermove(e: PointerEvent) {
		if (!points.length) return;
		const r = (e.currentTarget as SVGElement).getBoundingClientRect();
		const x = box.x0 + ((e.clientX - r.left) / r.width) * (box.x1 - box.x0);
		let best = 0;
		for (let i = 1; i < points.length; i++)
			if (Math.abs(points[i][0] - x) < Math.abs(points[best][0] - x)) best = i;
		at = best;
	}
	const hovered = $derived(at == null ? null : points[at]);
</script>

<!-- A curve with no axes or grid (H7): the shape is what's read here; the run's chart has the
     numbers. Hover reads one step. -->
<div class={['relative', className]} bind:clientWidth={W} bind:clientHeight={H}>
	<svg
		viewBox="0 0 {W} {H}"
		class="absolute inset-0 block size-full overflow-visible"
		role={interactive ? 'img' : 'presentation'}
		aria-hidden={!interactive}
		onpointermove={interactive ? onpointermove : undefined}
		onpointerleave={interactive ? () => (at = null) : undefined}
	>
		{#if area && fill}
			<path d={fill} class="fill-link/6 {draw ? 'spark-fade' : ''}" />
		{/if}
		<path
			{d}
			pathLength="1"
			class="fill-none {tone} {draw ? 'spark-draw' : ''}"
			stroke-width="1.5"
			stroke-linejoin="round"
			stroke-linecap="round"
		/>
		{#if dot && last}
			{#if live}
				<circle cx={sx(last[0])} cy={lastY} r="6" class="breathe fill-link/25" />
			{/if}
			<circle cx={sx(last[0])} cy={lastY} r="3" class="fill-link {draw ? 'spark-fade' : ''}" />
		{/if}
		{#if hovered}
			<line x1={sx(hovered[0])} x2={sx(hovered[0])} y1="0" y2={H} class="stroke-divider" />
		{/if}
	</svg>
	{#if end && last}
		<span
			class={[
				'end-halo pointer-events-none absolute right-0 text-right leading-tight',
				draw && 'spark-fade'
			]}
			style:bottom={endAbove ? `${H - lastY + 10}px` : undefined}
			style:top={endAbove ? undefined : `${lastY + 10}px`}
			class:opacity-0={hovered != null}
		>
			{@render end()}
		</span>
	{/if}
	{#if hovered}
		<span
			class="pointer-events-none absolute top-0 rounded bg-popover px-1.5 py-0.5 text-[11px] whitespace-nowrap tabular-nums shadow ring-1 ring-divider"
			style:left="{(sx(hovered[0]) / W) * 100}%"
			style:transform="translateX({sx(hovered[0]) > W / 2 ? '-105%' : '5%'})"
		>
			<span class="text-muted-foreground">{formatStep(hovered[0])}</span>
			{formatNumber(hovered[1])}
		</span>
	{/if}
</div>

<style>
	.spark-draw {
		stroke-dasharray: 1;
		stroke-dashoffset: 1;
		animation: spark-draw 900ms cubic-bezier(0.25, 0.8, 0.3, 1) 180ms forwards;
	}
	.spark-fade {
		opacity: 0;
		animation: spark-fade 600ms ease-out 600ms forwards;
	}
	/* The end's reading over a curve that runs through it: outlined in the tile's colour, so the
	   line breaks around the text instead of striking it. */
	.end-halo :global(*) {
		-webkit-text-stroke: 4px var(--card);
		paint-order: stroke fill;
	}
	/* A live run's end: a ring that breathes slowly, saying it is still logging. */
	.breathe {
		transform-box: fill-box;
		transform-origin: center;
		animation: breathe 2.4s ease-in-out infinite;
	}
	@keyframes breathe {
		0%,
		100% {
			opacity: 0.2;
			transform: scale(0.6);
		}
		50% {
			opacity: 1;
			transform: scale(1);
		}
	}
	@keyframes spark-draw {
		to {
			stroke-dashoffset: 0;
		}
	}
	@keyframes spark-fade {
		to {
			opacity: 1;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.spark-draw,
		.spark-fade,
		.breathe {
			animation: none;
			stroke-dashoffset: 0;
			opacity: 1;
		}
	}
</style>
