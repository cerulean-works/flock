<script lang="ts">
	import Tile from '#lib/components/home/Tile.svelte';
	import type { Budget } from '#lib/types.ts';
	import { money } from '#lib/utils.ts';
	import { orgHref } from '#lib/workspace/url.ts';

	let { budget, index }: { budget: Budget; index: number } = $props();

	const fmt = $derived((v: number) => money(v, budget.currency));
	const used = $derived(budget.budget ? budget.spent / budget.budget : 0);
	const over = $derived(budget.remaining != null && budget.remaining < 0);
	// Quiet while fine; amber from 80%, red over. Static class strings.
	const tone = $derived(
		over ? 'bg-state-failed' : used >= 0.8 ? 'bg-state-preempting' : 'bg-foreground/35'
	);
</script>

<!-- A 1x1 number with no title (Harry: the text says what it is): what's left of the month's
     budget, of how much, and a bar on the bottom edge as long as the month spent. -->
<Tile kind="number" title="Budget" bare {index}>
	<a href={orgHref(budget.org, 'budget')} class="flex flex-1 flex-col">
		{#if budget.budget && budget.remaining != null}
			<p class="text-[28px] leading-none font-semibold tracking-[-0.02em] tabular-nums">
				{fmt(Math.abs(budget.remaining))}
			</p>
			<p class="mt-1.5 text-[12px] text-muted-foreground">
				{over ? 'over' : 'left'} of {fmt(budget.budget)}
			</p>
			<div class="mt-auto h-1 overflow-hidden rounded-full bg-muted">
				<div
					class="bar h-full rounded-full {tone}"
					style:width="{Math.min(100, used * 100)}%"
				></div>
			</div>
		{:else}
			<p class="text-[28px] leading-none font-semibold tracking-[-0.02em] tabular-nums">
				{fmt(budget.spent)}
			</p>
			<p class="mt-1.5 text-[12px] text-muted-foreground">spent this month</p>
		{/if}
	</a>
</Tile>

<style>
	.bar {
		transform-origin: left;
		animation: bar-in 700ms cubic-bezier(0.25, 0.8, 0.3, 1) calc(200ms + var(--i, 0) * 45ms) both;
	}
	@keyframes bar-in {
		from {
			transform: scaleX(0);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.bar {
			animation: none;
		}
	}
</style>
