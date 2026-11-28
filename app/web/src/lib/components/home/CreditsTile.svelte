<script lang="ts">
	import Tile from '#lib/components/home/Tile.svelte';
	import { credits as fmt, type Credits } from '#lib/home/credits.ts';

	interface Props {
		credits: Credits | null;
		/** Configured, but it didn't answer. */
		down?: boolean;
		index: number;
	}

	let { credits, down = false, index }: Props = $props();

	/** The share of the grant still left: the bar reads as the number above it. */
	const left = $derived(
		credits && credits.granted > 0 ? Math.min(1, Math.max(0, credits.balance) / credits.granted) : 0
	);
	// Quiet while fine; amber once a fifth is left, red once it's gone.
	const tone = $derived(
		left <= 0 ? 'bg-state-failed' : left <= 0.2 ? 'bg-state-preempting' : 'bg-foreground/35'
	);
</script>

<!-- A 1x1 number, as the credit dashboard reads the NCHC wallet: what's left,
     and a bar as long as what's left. Opens the dashboard. -->
<Tile kind="number" title="Credits" bare {index}>
	{#if credits}
		<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- another service -->
		<a href={credits.url} target="_blank" rel="noopener" class="flex flex-1 flex-col">
			<p class="text-[28px] leading-none font-semibold tracking-[-0.02em] tabular-nums">
				{fmt(credits.balance)}
			</p>
			<p class="mt-1.5 text-[12px] text-muted-foreground">NTD credits left</p>
			<div class="mt-auto h-1 overflow-hidden rounded-full bg-muted">
				<div class="bar h-full rounded-full {tone}" style:width="{left * 100}%"></div>
			</div>
		</a>
	{:else if down}
		<div class="flex flex-1 flex-col">
			<p class="flex items-center gap-2 text-[15px] font-medium">
				<span class="size-1.5 rounded-full bg-state-failed"></span>Credits not reachable
			</p>
			<p class="mt-1.5 text-[12px] text-muted-foreground">The credit dashboard didn’t answer</p>
		</div>
	{/if}
</Tile>

<style>
	.bar {
		transform-origin: left;
		animation: bar-in 700ms cubic-bezier(0.25, 0.8, 0.3, 1) calc(360ms + var(--i, 0) * 45ms) both;
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
