<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import XIcon from '@lucide/svelte/icons/x';
	import type { ChipParts } from '#lib/workspace/filters.ts';

	/**
	 * A filter in effect, Linear's way: the column, the comparison (muted) and the value, with a
	 * cross that drops it. With `onclick`, the chip itself opens the filters to change it.
	 */

	interface Props {
		parts: ChipParts;
		onremove: () => void;
		onclick?: () => void;
	}

	let { parts, onremove, onclick }: Props = $props();

	const text = $derived([parts.label, parts.op, parts.value].filter(Boolean).join(' '));
	const FOCUS = 'outline-none focus-visible:ring-3 focus-visible:ring-ring/50';
</script>

{#snippet body()}
	{#if parts.label}<span class="flex-none text-foreground">{parts.label}</span>{/if}
	{#if parts.op}<span class="flex-none text-muted-foreground">{parts.op}</span>{/if}
	{#if parts.value}<span class="min-w-0 truncate text-foreground">{parts.value}</span>{/if}
{/snippet}

<span class="inline-flex h-6 max-w-80 min-w-0 items-center rounded-md bg-muted text-xs">
	{#if onclick}
		<button
			type="button"
			class="flex min-w-0 items-center gap-1 rounded-l-md py-0.5 pr-1 pl-2 {FOCUS}"
			{onclick}
			{@attach hint(text)}
		>
			{@render body()}
		</button>
	{:else}
		<span class="flex min-w-0 items-center gap-1 py-0.5 pr-1 pl-2" {@attach hint(text)}>
			{@render body()}
		</span>
	{/if}
	<button
		type="button"
		class="grid h-6 w-6 flex-none place-items-center rounded-r-md text-muted-foreground hover:text-foreground {FOCUS}"
		aria-label="Remove filter {text}"
		onclick={onremove}
	>
		<XIcon class="size-3" />
	</button>
</span>
