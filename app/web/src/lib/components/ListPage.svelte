<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import SelectionBar from '#lib/components/SelectionBar.svelte';
	import type { Selection } from '#lib/selection.svelte.ts';

	interface Props extends HTMLAttributes<HTMLElement> {
		title: string;
		/** How many runs are live here, for the header's mark. */
		live?: number;
		/** Beside the title: a ⋯ menu, a badge. */
		badges?: Snippet;
		/** Muted text on the title's line: a count of running runs. */
		subtitle?: Snippet;
		/** At the right of the title row: the page's own buttons. */
		actions?: Snippet;
		/** The toolbar's left side: what the list is, as words... */
		heading?: string;
		/** ...or as a snippet, such as breadcrumbs. */
		location?: Snippet;
		/** The toolbar's right side: filters, views, a URI to copy. */
		controls?: Snippet;
		/** While rows are checked, the toolbar's left side is the selection and these actions. */
		selection?: Selection | null;
		selected?: Snippet;
		/** The list, or what stands in for it (an empty state). */
		children: Snippet;
	}

	let {
		title,
		live = 0,
		badges,
		subtitle,
		actions,
		heading,
		location,
		controls,
		selection = null,
		selected,
		children,
		...main
	}: Props = $props();

	const selecting = $derived(!!selection?.size && !!selected);
	const toolbar = $derived(heading || location || controls || selecting ? bar : undefined);
</script>

<!--
	Every list page (an org's runs, datasets, models, storage and a bucket's objects): the title
	row with the page's buttons, a toolbar saying what the list is with its controls, and the list
	under them, the only part that scrolls.
-->
{#snippet bar()}
	<div class="flex min-w-0 items-center">
		{#if selecting && selection && selected}
			<SelectionBar {selection}>{@render selected()}</SelectionBar>
		{:else if location}
			{@render location()}
		{:else if heading}
			<h2 class="text-[13px] font-medium whitespace-nowrap max-sm:sr-only">{heading}</h2>
		{/if}
	</div>
	{#if controls}
		<div class="ml-auto flex flex-none items-center gap-1">{@render controls()}</div>
	{/if}
{/snippet}

<Page width="full">
	<PageHeader {title} {live} {badges} {subtitle} {actions} {toolbar} />
	<main {...main} class={['relative min-h-0 flex-1 overflow-y-auto pb-10', main.class]}>
		{@render children()}
	</main>
</Page>
