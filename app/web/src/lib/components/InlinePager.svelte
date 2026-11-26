<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { hint } from '#lib/hint.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';

	interface Props {
		/** The rows shown, 1-based, and how many there are (`60+` while more can load). */
		from: number;
		to: number;
		total: string;
		label: string;
		/** Newer and older pages; absent when there's none that way. */
		onprev?: () => void;
		onnext?: () => void;
		pending?: boolean;
		failed?: boolean;
	}

	let { from, to, total, label, onprev, onnext, pending = false, failed = false }: Props = $props();
</script>

<!-- The pager's inline form (Nov 24): under a list that's part of a page, not floating. -->
<nav class="flex items-center gap-1 text-[13px] tabular-nums" aria-label={label}>
	<span>{from}–{to}</span>
	<span class="mr-1 text-muted-foreground">of {total}</span>
	<Button
		variant="quiet"
		size="icon-bar"
		disabled={!onprev}
		aria-label="Newer"
		{@attach hint('Newer')}
		onclick={onprev}
	>
		<ChevronLeftIcon />
	</Button>
	<Button
		variant="quiet"
		size="icon-bar"
		disabled={!onnext}
		{pending}
		aria-label="Older"
		{@attach hint('Older')}
		onclick={onnext}
	>
		<ChevronRightIcon />
	</Button>
	{#if failed}<span class="text-destructive">Couldn’t load more</span>{/if}
</nav>
