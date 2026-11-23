<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { Button } from '#lib/components/ui/button/index.js';
	import { pageNumbers } from '#lib/datasets.ts';

	/**
	 * A pager under a table: which rows are shown of how many, then Previous, the page numbers
	 * (the first, the last and the current one's neighbours) and Next.
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
	}

	let { current, pages, first, last, total, label, ongo, onstep }: Props = $props();
</script>

<nav
	class="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] tabular-nums"
	aria-label={label}
>
	<p>
		{first.toLocaleString('en-US')}–{last.toLocaleString('en-US')}
		<span class="text-muted-foreground">of {total.toLocaleString('en-US')}</span>
	</p>
	<div class="ml-auto flex items-center gap-1">
		<Button
			variant="ghost"
			size="sm"
			aria-label="Previous page"
			disabled={first <= 1}
			onclick={() => (onstep ? onstep(-1) : ongo(current - 1))}
		>
			<ChevronLeftIcon /> Previous
		</Button>
		{#each pageNumbers(current, pages) as p, i (p ?? `gap${i}`)}
			{#if p === null}
				<span class="w-7 text-center text-muted-foreground" aria-hidden="true">…</span>
			{:else}
				<Button
					variant={p === current ? 'secondary' : 'ghost'}
					size="sm"
					class="min-w-7 px-1.5"
					aria-label="Page {p}"
					aria-current={p === current ? 'page' : undefined}
					onclick={() => ongo(p)}
				>
					{p.toLocaleString('en-US')}
				</Button>
			{/if}
		{/each}
		<Button
			variant="ghost"
			size="sm"
			aria-label="Next page"
			disabled={last >= total}
			onclick={() => (onstep ? onstep(1) : ongo(current + 1))}
		>
			Next <ChevronRightIcon />
		</Button>
	</div>
</nav>
