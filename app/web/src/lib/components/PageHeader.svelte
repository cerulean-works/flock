<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
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
	}

	let { title, badges, subtitle, subtitleTitle, actions, toolbar }: Props = $props();
</script>

<!--
	The header of the workspace pages (an org's runs, a project, a run). Every row has a fixed
	height and nothing wraps, so the title and the toolbar sit at the same place on each of them
	and moving between them never shifts the page.
-->
<header class="flex-none">
	<div class="box-content flex h-10 items-baseline gap-x-3 px-5 pt-5 pb-3">
		<div class="flex min-w-0 items-baseline gap-1">
			<h1 class="min-w-0 truncate text-[34px] leading-10 font-normal tracking-[-0.025em]" {title}>
				{title}
			</h1>
			{#if badges}
				<div class="flex flex-none items-center gap-2 self-center">{@render badges()}</div>
			{/if}
		</div>
		{#if subtitle}
			<p
				class="min-w-0 flex-1 truncate text-[13px] text-muted-foreground max-sm:hidden"
				title={subtitleTitle}
			>
				{@render subtitle()}
			</p>
		{/if}
		{#if actions}
			<div class="ml-auto flex flex-none items-center gap-2 self-center">{@render actions()}</div>
		{/if}
	</div>
	{#if toolbar}
		<div class="flex h-12 items-center gap-2 border-b px-5">{@render toolbar()}</div>
	{/if}
</header>
