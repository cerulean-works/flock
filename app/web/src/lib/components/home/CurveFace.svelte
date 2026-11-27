<script lang="ts">
	import type { Snippet } from 'svelte';
	import Spark from '#lib/components/home/Spark.svelte';
	import { reading, type Curve } from '#lib/home/curves.ts';
	import { formatNumber } from '#lib/utils.ts';

	interface Props {
		metric: string;
		curve: Curve;
		draw?: boolean;
		/** The line above the value: the metric's name by default. */
		label?: Snippet;
	}

	let { metric, curve, draw = false, label }: Props = $props();

	const r = $derived(reading(curve));
	// Losses read well going down; the rest as plain change.
	const lower = $derived(/loss|error|perplexity|ppl/i.test(metric));
	const good = $derived(
		r.delta == null || r.delta === 0 ? null : lower ? r.delta < 0 : r.delta > 0
	);
</script>

<!-- One curve as a plot tile shows it: what it is, its latest value and recent change on the
     left, its shape filling the rest (a 2x1 strip). -->
<div class="flex min-h-0 flex-1 gap-4">
	<div class="flex w-32 flex-none flex-col">
		<div class="-mt-1 mb-2 min-w-0 truncate text-[12px] text-muted-foreground">
			{#if label}{@render label()}{:else}{metric}{/if}
		</div>
		<span class="text-[22px] leading-none font-medium tracking-[-0.01em] tabular-nums">
			{r.last == null ? '–' : formatNumber(r.last)}
		</span>
		{#if r.delta != null && r.delta !== 0}
			<span
				class={[
					'mt-1.5 text-[12px] tabular-nums',
					good ? 'text-state-finished' : 'text-muted-foreground'
				]}
			>
				{r.delta < 0 ? '↓' : '↑'}
				{formatNumber(Math.abs(r.delta))}
			</span>
		{/if}
	</div>
	<Spark {curve} {draw} interactive area class="min-h-16 min-w-0 flex-1" />
</div>
