<script lang="ts">
	import Tile from '#lib/components/home/Tile.svelte';
	import type { ClusterGlance } from '#lib/home/cluster.ts';

	interface Props {
		cluster: ClusterGlance | null;
		/** Configured, but it didn't answer. */
		down?: boolean;
		index: number;
	}

	let { cluster, down = false, index }: Props = $props();

	const share = $derived(
		cluster?.capacity ? Math.min(1, cluster.allocated / cluster.capacity) : null
	);
	/** GPUs held past capacity (the queue's oversubscription), else 0. */
	const over = $derived(cluster?.capacity ? Math.max(0, cluster.allocated - cluster.capacity) : 0);
</script>

<!-- A 1x1 number (Apple's small widget): GPUs the clusters' running jobs hold, the queue
     behind them. Opens the Slurm dashboard. -->
<Tile
	kind="number"
	title="Cluster"
	bare
	{index}
	class={!cluster && !down ? 'opacity-45' : undefined}
>
	<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- another service -->
	<a href={cluster?.href} target="_blank" rel="noopener" class="flex flex-1 flex-col">
		{#if cluster}
			<p class="flex items-baseline gap-1">
				<span
					class={[
						'text-[28px] leading-none font-semibold tracking-[-0.02em] tabular-nums',
						over && 'text-state-preempting'
					]}>{cluster.allocated}</span
				>
				{#if cluster.capacity}
					<span class="text-[15px] text-muted-foreground tabular-nums">/{cluster.capacity}</span>
				{/if}
			</p>
			<p class="mt-1.5 text-[12px] text-muted-foreground">
				{#if over}<span class="text-state-preempting">{over} over capacity</span>{:else}GPUs in use{/if}
				· {cluster.pending} queued
			</p>
			{#if share != null}
				<div class="mt-auto h-1 overflow-hidden rounded-full bg-muted">
					<div
						class={['bar h-full rounded-full', over ? 'bg-state-preempting' : 'bg-foreground/35']}
						style:width="{share * 100}%"
					></div>
				</div>
			{/if}
		{:else if !down}
			<!-- No dashboard configured: the cell stays, faded, saying so. -->
			<p class="text-[15px] font-medium text-muted-foreground">No cluster connected</p>
			<div class="mt-auto h-1 rounded-full bg-muted"></div>
		{:else}
			<p class="flex items-center gap-2 text-[15px] font-medium">
				<span class="size-1.5 rounded-full bg-state-failed"></span>Cluster not reachable
			</p>
			<p class="mt-1.5 text-[12px] text-muted-foreground">The Slurm dashboard didn’t answer</p>
		{/if}
	</a>
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
