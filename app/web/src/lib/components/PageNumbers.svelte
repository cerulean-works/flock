<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { Button } from '#lib/components/ui/button/index.js';
	import { hint } from '#lib/hint.svelte.ts';
	import PageSizeMenu from './PageSizeMenu.svelte';
	import { PAGER_PILL } from './Pager.svelte';
	import PagerRange from './PagerRange.svelte';

	/**
	 * A pager for a list in a page's content (the dataset viewer, a run's eval items): the runs
	 * table's floating pill (Gate 2), pinned at the foot of the scroll as the list goes by. Which
	 * rows are shown of how many, then the page before and after.
	 */
	interface Props {
		/** The current page, from 1. */
		current: number;
		pages: number;
		/** The first and last row shown, from 1, and how many there are in all. */
		first: number;
		last: number;
		total: number;
		/** The navigation's name, such as `Row pages`. */
		label: string;
		/** Go to a page, from 1. */
		ongo: (page: number) => void;
		/**
		 * Previous and Next, where they don't land on a page: a list opened at any row moves a
		 * page's length from it. Without it they go to the page before or after.
		 */
		onstep?: (step: -1 | 1) => void;
		/** Rows to a page, the sizes offered and what's counted ("Rows per page"), as the other pagers. */
		size?: number;
		sizes?: readonly number[];
		sizeLabel?: string;
		onsize?: (size: number) => void;
	}

	let {
		current,
		pages,
		first,
		last,
		total,
		label,
		ongo,
		onstep,
		size,
		sizes = [],
		sizeLabel = 'Rows per page',
		onsize
	}: Props = $props();

	const step = (by: -1 | 1) => (onstep ? onstep(by) : ongo(current + by));
</script>

<div class="sticky bottom-3.5 z-5 w-fit">
	<nav class={PAGER_PILL} aria-label={label}>
		<PagerRange from={first} to={last} most={total} />
		<span class="mr-1 text-muted-foreground">of {total.toLocaleString('en-US')}</span>
		<Button
			variant="ghost"
			size="icon-sm"
			class="disabled:opacity-40"
			aria-label="Previous page"
			{@attach hint('Previous page')}
			disabled={first <= 1}
			onclick={() => step(-1)}
		>
			<ChevronLeftIcon />
		</Button>
		<Button
			variant="ghost"
			size="icon-sm"
			class="disabled:opacity-40"
			aria-label="Next page"
			{@attach hint('Next page')}
			disabled={last >= total || current >= pages}
			onclick={() => step(1)}
		>
			<ChevronRightIcon />
		</Button>
		{#if size && onsize && sizes.length}
			<PageSizeMenu {size} {sizes} label={sizeLabel} {onsize} />
		{/if}
	</nav>
</div>
