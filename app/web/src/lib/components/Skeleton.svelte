<script lang="ts">
	import { cn } from '#lib/utils.js';

	/**
	 * What's loading, sketched as faint bars in the shape it will take, so nothing jumps when it
	 * arrives: a table's rows, a few lines of text, or one panel. It holds its place from the start
	 * but shows only after a moment, so a fast load never flashes it.
	 */
	interface Props {
		shape?: 'rows' | 'lines' | 'block';
		/** Rows or lines to sketch. */
		count?: number;
		class?: string;
	}

	let { shape = 'rows', count = shape === 'rows' ? 6 : 3, class: className }: Props = $props();

	let shown = $state(false);
	$effect(() => {
		const timer = setTimeout(() => (shown = true), 200);
		return () => clearTimeout(timer);
	});

	// Widths that vary but stay put between renders, so a sketch reads as text, not stripes.
	const WIDTHS = [62, 44, 78, 53, 70, 38, 66, 49];
	const width = (i: number) => WIDTHS[i % WIDTHS.length];
</script>

<div
	role="status"
	aria-label="Loading"
	class={cn(!shown && 'invisible', shape === 'lines' && 'grid gap-2.5 py-1.5', className)}
>
	{#if shape === 'rows'}
		{#each { length: count }, i (i)}
			<div class="flex h-9 items-center gap-6 border-b border-divider px-3">
				<span class="h-2.5 animate-pulse rounded-full bg-muted" style:width="{width(i) * 0.5}%"
				></span>
				<span class="ml-auto h-2.5 w-14 animate-pulse rounded-full bg-muted"></span>
			</div>
		{/each}
	{:else if shape === 'lines'}
		{#each { length: count }, i (i)}
			<span class="h-2.5 animate-pulse rounded-full bg-muted" style:width="{width(i)}%"></span>
		{/each}
	{:else}
		<div class="h-full min-h-24 animate-pulse bg-card"></div>
	{/if}
</div>
