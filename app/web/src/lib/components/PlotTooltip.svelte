<script lang="ts" module>
	import type { TooltipMetrics } from '#lib/chart/readout.ts';

	/** One line's reading, formatted. */
	export interface TipRow {
		key: string;
		color: string;
		value: string;
		/** The unsmoothed value, when smoothing is on. */
		raw: string | null;
		name: string;
		/** The line's metric, on panels of several metrics with several runs. */
		metric: string | null;
		/** Where the line's point is, when it logged none at the crosshair's step. */
		step: string | null;
		focused: boolean;
	}

	/** A plot's readout at the synced crosshair (see LinePlot). */
	export interface Tip {
		/** Where it goes: in the window's coordinates if `float`, else the plot's. */
		left: number;
		top: number;
		/**
		 * Over the page, in a layer of its own (every grid plot's, else the hovered plot's), rather than inside the plot,
		 * which a card's edges and its pane's scrolling would clip.
		 */
		float: boolean;
		/** The hovered plot's: stacked over the other plots' floating tooltips. */
		hovered: boolean;
		width: number;
		/** The crosshair's x: `Step 1,200`, `Runtime 3m 05s`... */
		header: string;
		rows: TipRow[];
		above: number;
		below: number;
	}

	/** Sizes the markup below renders at, which LinePlot lays tooltips out by. */
	export const TIP_METRICS: Record<'compact' | 'roomy', TooltipMetrics> = {
		// Header (h-5 + mb-0.5) and the box's p-1.
		compact: { chrome: 20 + 2 + 8, row: 18, more: 16 },
		// Header (h-6 + mb-1) and the box's p-1.
		roomy: { chrome: 24 + 4 + 8, row: 22, more: 18 }
	};
	export const TIP_WIDTH = { compact: 248, roomy: 320 };
</script>

<script lang="ts">
	interface Props {
		tip: Tip;
		/** Grid cards: smaller rows, so more of them fit. */
		compact: boolean;
	}

	let { tip, compact }: Props = $props();

	// Puts it back on cleanup: when the plot goes from hovered to following, the same element
	// stays mounted and must return to the plot.
	function toBody(el: HTMLElement) {
		const parent = el.parentNode;
		const next = el.nextSibling;
		document.body.append(el);
		return () => {
			if (parent?.isConnected) parent.insertBefore(el, next);
			else el.remove();
		};
	}
</script>

<!-- Every plot following the cursor shows one; none takes the pointer, so the next plot can
     still be hovered. Floating ones (every grid plot's) are moved to the end of the document, out of any card, pane or dialog that would clip it or
     position it (a transformed ancestor would). -->
<div class="contents">
	<div
		{@attach tip.float ? toBody : undefined}
		class={[
			'pointer-events-none top-0 left-0 rounded-lg',
			tip.float ? ['fixed', tip.hovered ? 'z-61' : 'z-60'] : 'absolute z-10',
			'bg-popover/95 p-1 text-popover-foreground tabular-nums shadow-md ring-1 ring-foreground/10 will-change-transform',
			compact ? 'text-[11px]' : 'text-xs'
		]}
		style:width="{tip.width}px"
		style:transform="translate({tip.left}px, {tip.top}px)"
		aria-hidden="true"
	>
		<div
			class={[
				'flex items-center border-b border-divider px-2 text-muted-foreground',
				compact ? 'mb-0.5 h-5' : 'mb-1 h-6'
			]}
		>
			<span class="truncate">{tip.header}</span>
		</div>
		{#if tip.above}
			<div class={['flex items-center px-2 text-faint', compact ? 'h-4' : 'h-[18px]']}>
				{tip.above} more above
			</div>
		{/if}
		{#each tip.rows as r (r.key)}
			<div
				class={[
					'flex items-center gap-2 rounded-md px-2',
					compact ? 'h-[18px]' : 'h-[22px]',
					r.focused ? 'bg-accent text-foreground' : 'text-muted-foreground'
				]}
			>
				<span
					class={['w-3 flex-none rounded-full', r.focused ? 'h-[3px]' : 'h-0.5']}
					style:background={r.color}
				></span>
				<span
					class={['flex-none font-semibold text-foreground', compact ? 'min-w-12' : 'min-w-[58px]']}
					>{r.value}</span
				>
				{#if r.raw != null}<span class="flex-none text-faint">{r.raw}</span>{/if}
				<span class="truncate"
					>{r.name}{#if r.metric}<span class="text-faint"> · {r.metric}</span>{/if}</span
				>
				{#if r.step}
					<span class="ml-auto flex-none text-faint">{r.step}</span>
				{/if}
			</div>
		{/each}
		{#if tip.below}
			<div class={['flex items-center px-2 text-faint', compact ? 'h-4' : 'h-[18px]']}>
				{tip.below} more below
			</div>
		{/if}
	</div>
</div>
