<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { hint } from '#lib/hint.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';

	interface Props {
		/** The rows shown, 1-based, and how many there are (`many` while more can load). */
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

	let nav = $state<HTMLElement>();

	/** Turns the page, then shows its top: the list scrolls in the page's main area. */
	async function turn(go?: () => void | Promise<void>) {
		await go?.();
		nav?.closest('main')?.scrollTo({ top: 0 });
	}
</script>

<!-- The pager's inline form (Nov 24): under a list that's part of a page, not floating. -->
<nav bind:this={nav} class="flex items-center gap-1 text-[13px] tabular-nums" aria-label={label}>
	<span>{from}–{to}</span>
	<!-- A total only once it's known: the feed loads as it pages. -->
	{#if total !== 'many'}<span class="mr-1 text-muted-foreground">of {total}</span>{:else}<span
			class="mr-1"
		></span>{/if}
	<Button
		variant="quiet"
		size="icon-bar"
		disabled={!onprev}
		aria-label="Newer"
		{@attach hint('Newer')}
		onclick={() => turn(onprev)}
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
		onclick={() => turn(onnext)}
	>
		<ChevronRightIcon />
	</Button>
	{#if failed}<span class="text-destructive">Couldn’t load more</span>{/if}
</nav>
