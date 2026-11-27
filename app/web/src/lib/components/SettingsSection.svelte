<script lang="ts">
	import type { Snippet } from 'svelte';
	import { SETTINGS_HEADING } from '#lib/settings.ts';

	interface Props {
		/** Its anchor, and its entry in the settings sidebar. */
		id: string;
		title: string;
		/** How many things it lists (members, accounts), muted beside the heading. */
		count?: number;
		children: Snippet;
	}

	let { id, title, count, children }: Props = $props();
</script>

<!-- One section of a settings page: a hairline and 48px between sections, its heading on top. -->
<section
	{id}
	data-toc
	class="scroll-mt-8 border-t border-divider pt-10 not-first:mt-12 first:border-t-0 first:pt-0"
	aria-labelledby="{id}-heading"
>
	<h2 id="{id}-heading" class={['flex items-baseline gap-2', SETTINGS_HEADING]}>
		{title}
		{#if count != null}
			<span class="text-[13px] font-normal text-muted-foreground tabular-nums"
				>{count.toLocaleString('en-US')}</span
			>
		{/if}
	</h2>
	<div class="mt-5 [&>:first-child]:mt-0 [&>section:first-child]:mt-0">{@render children()}</div>
</section>
