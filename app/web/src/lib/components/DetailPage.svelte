<script lang="ts" generics="T extends string">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import PanelRightIcon from '@lucide/svelte/icons/panel-right';
	import type { Snippet } from 'svelte';
	import { hint } from '#lib/hint.svelte.ts';
	import { layerOpen, typing } from '#lib/keys.svelte.ts';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { pane } from '#lib/components/pane.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import type { Crumb } from '#lib/storage.ts';

	interface Props {
		/** The thing's name, the trail's last step. */
		title: string;
		/** The steps before it, such as Datasets › pretrain. */
		trail?: Crumb[];
		/** Lead the trail with the org (off on Datasets, Models and Storage). */
		org?: boolean;
		/** Hover text for the title, which may be cut short: the full path. */
		titleHint?: string;
		/** Beside the title: the version picker, muted words. */
		badges?: Snippet;
		/** At the right of the title row. */
		actions?: Snippet;
		/** The view bar's tabs; without them the page has no view bar. */
		tabs?: readonly { value: T; label: string }[];
		tab?: T;
		ontab?: (tab: T) => void;
		/** What the tabs are of, for screen readers. */
		tabsLabel?: string;
		/** The view bar's right side (the title row's, without tabs): the page's actions, quiet. */
		controls?: Snippet;
		/** The properties pane: right of the content, folded into a Details row above it on a
		 * phone. `]` hides it. */
		properties?: Snippet;
		/** Muted beside the phone's Details row: the one or two facts worth seeing folded. */
		summary?: Snippet;
		/** The content runs to the edges, as a full-page table does: no gutter or top padding. */
		flush?: boolean;
		/** The content fills the page and scrolls itself (charts, a log): flush, no bottom room. */
		fill?: boolean;
		/** The content, the part that scrolls. */
		children: Snippet;
	}

	let {
		title,
		trail = [],
		org = false,
		titleHint,
		badges,
		actions,
		tabs,
		tab,
		ontab,
		tabsLabel,
		controls,
		properties,
		summary,
		flush = false,
		fill = false,
		children
	}: Props = $props();

	const shown = $derived(!!properties && pane.open);
	/** The phone's Details row, open: for this page only. */
	let details = $state(false);

	function onkeydown(e: KeyboardEvent) {
		if (!properties || e.key !== ']' || e.defaultPrevented || typing(e) || layerOpen()) return;
		e.preventDefault();
		pane.open = !pane.open;
	}
</script>

<svelte:window {onkeydown} />

<!--
	A thing's page (a dataset, a model, an object, a run), Linear's issue page: the trail ending
	in its name, a view bar with its tabs and actions, the content, and its properties in a pane
	at the right.
-->
{#snippet toggle()}
	{#if properties}
		<Button
			variant={pane.open ? 'quiet-on' : 'quiet'}
			size="icon-bar"
			class="max-md:hidden"
			aria-label={pane.open ? 'Hide properties' : 'Show properties'}
			aria-pressed={pane.open}
			{@attach hint(pane.open ? 'Hide properties  ]' : 'Show properties  ]')}
			onclick={() => (pane.open = !pane.open)}
		>
			<PanelRightIcon />
		</Button>
	{/if}
{/snippet}

<!-- With tabs, the view bar holds them and the actions; without, the actions join the title row. -->
{#snippet bar()}
	<Tabs.Root value={tab} onValueChange={(v) => ontab?.(v as T)} class="min-w-0">
		<Tabs.List aria-label={tabsLabel}>
			{#each tabs ?? [] as t (t.value)}
				<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
			{/each}
		</Tabs.List>
	</Tabs.Root>
	<div class="ml-auto flex flex-none items-center gap-0.5">
		{#if controls}{@render controls()}{/if}
		<!-- On a tab without the pane its place stays, so the actions don't move from tab to tab. -->
		{#if properties}{@render toggle()}{:else}<span class="size-7 max-md:hidden" aria-hidden="true"
			></span>{/if}
	</div>
{/snippet}

{#snippet headerActions()}
	{#if actions}{@render actions()}{/if}
	{#if !tabs}
		<div class="flex items-center gap-0.5">
			{#if controls}{@render controls()}{/if}
			{@render toggle()}
		</div>
	{/if}
{/snippet}

<Page width="full">
	<PageHeader
		{title}
		{trail}
		{org}
		subtitleTitle={titleHint}
		{badges}
		actions={headerActions}
		toolbar={tabs ? bar : undefined}
	/>
	<div class="flex min-h-0 flex-1 max-md:flex-col">
		{#if properties}
			<aside
				aria-label="Properties"
				class={[
					'flex-none md:order-last md:w-70 md:overflow-y-auto md:border-l md:border-divider',
					'max-md:border-b max-md:border-divider',
					!shown && 'md:hidden'
				]}
			>
				<!-- On a phone the pane folds into one row above the content, so the content leads. -->
				<button
					type="button"
					class="flex h-9 w-full items-center gap-2 px-5 text-left text-[13px] outline-none focus-visible:bg-muted md:hidden"
					aria-expanded={details}
					aria-controls="detail-properties"
					onclick={() => (details = !details)}
				>
					<span class="flex-none font-medium">Details</span>
					{#if summary}
						<span class="flex min-w-0 items-center gap-2 truncate text-muted-foreground"
							>{@render summary()}</span
						>
					{/if}
					<ChevronDownIcon
						class={[
							'ml-auto size-4 flex-none text-muted-foreground transition-transform',
							details && 'rotate-180'
						]}
					/>
				</button>
				<div
					id="detail-properties"
					class={[
						'px-5 py-4 max-md:max-h-[55dvh] max-md:overflow-y-auto max-md:pt-0',
						!details && 'max-md:hidden'
					]}
				>
					{@render properties()}
				</div>
			</aside>
		{/if}
		{#if fill}
			<main class="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
				{@render children()}
			</main>
		{:else}
			<main
				class={[
					'@container relative min-h-0 min-w-0 flex-1 overflow-y-auto',
					!flush && 'px-5 pt-5'
				]}
			>
				<!-- The bottom room is inside, not the scroll's padding: that would hold a floating
				     pager that far off the scroll's foot. -->
				<div class="pb-16">{@render children()}</div>
			</main>
		{/if}
	</div>
</Page>
