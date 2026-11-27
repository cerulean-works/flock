<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { hint } from '#lib/hint.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';
	import PageSizeMenu from './PageSizeMenu.svelte';
	import { PAGER_PILL } from './Pager.svelte';

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
		/** Rows to a page, the sizes offered and what's counted ("Runs per page"). */
		size?: number;
		sizes?: readonly number[];
		sizeLabel?: string;
		onsize?: (size: number) => void;
	}

	let {
		from,
		to,
		total,
		label,
		onprev,
		onnext,
		pending = false,
		failed = false,
		size,
		sizes = [],
		sizeLabel = 'Rows per page',
		onsize
	}: Props = $props();

	let nav = $state<HTMLElement>();

	/** Turns the page, then shows its top: the list scrolls in the page's main area. */
	async function turn(go?: () => void | Promise<void>) {
		await go?.();
		nav?.closest('main')?.scrollTo({ top: 0 });
	}
</script>

<!-- The runs table's floating pager (Gate 2), for a list that loads as it pages: the same pill,
     the same buttons. Its place (pinned at the scroll's foot) is the page's to set. -->
<nav bind:this={nav} class={PAGER_PILL} aria-label={label}>
	<span>{from}–{to}</span>
	<!-- A total only once it's known: the feed loads as it pages. -->
	{#if total !== 'many'}<span class="mr-1 text-muted-foreground">of {total}</span>{:else}<span
			class="mr-1"
		></span>{/if}
	<Button
		variant="ghost"
		size="icon-sm"
		disabled={!onprev}
		class="disabled:opacity-40"
		aria-label="Newer"
		{@attach hint('Newer')}
		onclick={() => turn(onprev)}
	>
		<ChevronLeftIcon />
	</Button>
	<Button
		variant="ghost"
		size="icon-sm"
		disabled={!onnext}
		class="disabled:opacity-40"
		{pending}
		aria-label="Older"
		{@attach hint('Older')}
		onclick={() => turn(onnext)}
	>
		<ChevronRightIcon />
	</Button>
	{#if size && onsize}
		<PageSizeMenu {size} {sizes} label={sizeLabel} onsize={(n) => turn(() => onsize(n))} />
	{/if}
	{#if failed}<span class="px-2 text-destructive">Couldn’t load more</span>{/if}
</nav>
