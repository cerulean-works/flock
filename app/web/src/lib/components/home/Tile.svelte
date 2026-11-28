<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';

	interface Props extends Omit<HTMLAttributes<HTMLElement>, 'title' | 'children'> {
		/** The tile's kind sets its width (H9): a number or a plot is small, a run list half. */
		kind: 'number' | 'plot' | 'runs' | 'strip';
		/** The muted label at the top: what this is. */
		title: string;
		/** The title goes where the whole of it is: the runs table, the run's chart. */
		href?: string;
		/** At the title's right: a count, a month, a hover action. */
		meta?: Snippet;
		/** No title row: the content says what it is (the title still names it for screen readers). */
		bare?: boolean;
		/** The content scrolls inside the tile, faded at the edge while more is below. */
		scroll?: boolean;
		/** Its place in the arrival (H13), from 0. */
		index?: number;
		children: Snippet;
	}

	let {
		kind,
		title,
		href,
		meta,
		bare = false,
		scroll = false,
		index = 0,
		children,
		class: className,
		...rest
	}: Props = $props();

	/** Scrolled down: the top edge fades too, as rows pass under it. */
	let scrolled = $state(false);

	// Static class strings, so Tailwind sees each one.
	// Cells of a 4-column grid (Harry): a number is 1x1, a plot 2x1 (a wide strip), the runs
	// 2x2. Phones: two columns, numbers side by side, the rest full width.
	const SPAN = {
		number: 'col-span-1',
		plot: 'col-span-2 min-h-40',
		runs: 'col-span-2 md:row-span-2 md:min-h-0 md:overflow-hidden',
		/** As tall as its lines: placed by its parent (Operations, above the live plots). */
		strip: 'py-2'
	} as const;
</script>

<!-- A filled block with no border (the settled blocks rule): a label, then what it holds. -->
<section
	class={['tile group/tile flex min-w-0 flex-col rounded-2xl bg-card p-4', SPAN[kind], className]}
	style:--i={index}
	aria-label={title}
	{...rest}
>
	{#if !bare}
		<header class="mb-3 flex h-5 min-w-0 items-center gap-2 text-[12px] text-muted-foreground">
			{#if href}
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve() -->
				<a {href} class="min-w-0 truncate hover:text-foreground">{title}</a>
			{:else}
				<h2 class="min-w-0 truncate">{title}</h2>
			{/if}
			<span class="ml-auto flex flex-none items-center gap-1">{@render meta?.()}</span>
		</header>
	{/if}
	{#if scroll}
		<div
			class="tile-scroll -mx-4 -mb-4 min-h-0 flex-1 overflow-y-auto px-4 pt-0 pb-10 max-md:max-h-96"
			class:scrolled
			onscroll={(e) => (scrolled = e.currentTarget.scrollTop > 0)}
		>
			{@render children()}
		</div>
	{:else}
		<div class="flex min-h-0 flex-1 flex-col">{@render children()}</div>
	{/if}
</section>

<style>
	/* The top fade's depth, typed so it eases in and out rather than snapping. */
	@property --fade-top {
		syntax: '<length>';
		inherits: false;
		initial-value: 0px;
	}
	/* Rows fade out at the bottom edge; the padding under the last row lets it scroll clear.
	   Once scrolled, they fade at the top edge too. */
	.tile-scroll {
		mask-image: linear-gradient(
			to bottom,
			transparent,
			black var(--fade-top),
			black calc(100% - 2.5rem),
			transparent
		);
		scrollbar-width: none;
		transition: --fade-top 200ms ease-out;
	}
	.tile-scroll.scrolled {
		--fade-top: 2rem;
	}
</style>
