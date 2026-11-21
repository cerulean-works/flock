<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		/**
		 * `workspace` (the default): the fixed-height header of a full-width page. `home`: the
		 * header of a page at Home's width, which flows with the page and may wrap.
		 */
		layout?: 'workspace' | 'home';
		/** Before the title, centred on it: an org's tile, an icon. */
		leading?: Snippet;
		/** Beside the title, centred on it: its ⋯ menu, a state badge. */
		badges?: Snippet;
		/** Muted text on the title's baseline, cut to one line (a description, counts). */
		subtitle?: Snippet;
		/** Hover text for the subtitle, which may be cut short. */
		subtitleTitle?: string;
		/** At the right of the title row. */
		actions?: Snippet;
		/** The row under the title: view tabs, search, filters. Draws the header's bottom border. */
		toolbar?: Snippet;
		/** `home` only: a row above the title, such as breadcrumbs. */
		crumbs?: Snippet;
		/** `home` only: muted reading text under the title, which may wrap. */
		description?: Snippet;
	}

	let {
		title,
		layout = 'workspace',
		leading,
		badges,
		subtitle,
		subtitleTitle,
		actions,
		toolbar,
		crumbs,
		description
	}: Props = $props();
</script>

<!--
	A page's header. On the workspace pages (an org's runs, a project, a run) every row has a fixed
	height and nothing wraps, so the title and the toolbar sit at the same place on each of them
	and moving between them never shifts the page. On a `home` page the title row keeps its
	actions at the right edge, so a page's controls never sit half-stretched under the title.
-->
{#if layout === 'home'}
	<header class={['pb-5', crumbs ? 'pt-5' : 'pt-8']}>
		{#if crumbs}
			<div class="pb-3">{@render crumbs()}</div>
		{/if}
		<div class="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-2">
			<div class="flex min-w-0 flex-1 items-baseline gap-x-3">
				<div class="flex min-w-0 items-center gap-3">
					{#if leading}
						<div class="flex flex-none items-center">{@render leading()}</div>
					{/if}
					<h1
						class="min-w-0 truncate text-[34px] leading-[1.15] font-normal tracking-[-0.025em]"
						{title}
					>
						{title}
					</h1>
					{#if badges}
						<div class="flex flex-none items-center gap-2">{@render badges()}</div>
					{/if}
				</div>
				{#if subtitle}
					<div
						class="min-w-0 truncate text-[13px] text-muted-foreground tabular-nums max-sm:hidden"
						title={subtitleTitle}
					>
						{@render subtitle()}
					</div>
				{/if}
			</div>
			{#if actions}
				<div class="ml-auto flex flex-none items-center gap-2">{@render actions()}</div>
			{/if}
		</div>
		{#if description}
			<p class="mt-1 max-w-[62ch] text-muted-foreground">{@render description()}</p>
		{/if}
		{#if toolbar}
			<div class="mt-4 flex min-h-10 flex-wrap items-center gap-2">{@render toolbar()}</div>
		{/if}
	</header>
{:else}
	<header class="flex-none">
		<div class="box-content flex h-10 items-baseline gap-x-3 pt-5 pr-7 pb-3 pl-5">
			<div class="flex min-w-0 items-baseline gap-1">
				{#if leading}
					<div class="mr-2 flex flex-none items-center self-center">{@render leading()}</div>
				{/if}
				<!-- Pulled left by the big F's side bearing, so its stem lines up with the text below. -->
				<h1
					class="-ml-[0.045em] min-w-0 truncate text-[34px] leading-10 font-normal tracking-[-0.025em]"
					{title}
				>
					{title}
				</h1>
				{#if badges}
					<div class="flex flex-none items-center gap-2 self-center">{@render badges()}</div>
				{/if}
			</div>
			{#if subtitle}
				<div
					class="min-w-0 flex-1 truncate text-[13px] text-muted-foreground max-sm:hidden"
					title={subtitleTitle}
				>
					{@render subtitle()}
				</div>
			{/if}
			{#if actions}
				<div class="ml-auto flex flex-none items-center gap-2 self-center">{@render actions()}</div>
			{/if}
		</div>
		{#if toolbar}
			<div class="flex h-12 items-center gap-2 border-b pr-7 pl-5">{@render toolbar()}</div>
		{/if}
	</header>
{/if}
