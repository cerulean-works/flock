<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		/** The heading's id, for the section's `aria-labelledby`. */
		id?: string;
		/** Muted text on the heading's baseline, such as a count. */
		meta?: Snippet;
		/** At the right of the heading row: the section's buttons. */
		actions?: Snippet;
		/** Muted reading text under the heading, which may wrap. */
		description?: Snippet;
	}

	let { title, id, meta, actions, description }: Props = $props();
</script>

<!--
	A section's heading on a `home` page: the heading and its count at the left, the section's own
	buttons at the right edge, so its controls never sit half-stretched above a full-width table.
-->
<div>
	<div class="flex min-h-8 flex-wrap items-center gap-x-3 gap-y-2">
		<div class="flex min-w-0 flex-1 items-baseline gap-3">
			<h2 {id} class="min-w-0 truncate text-2xl tracking-[-0.01em]">{title}</h2>
			{#if meta}
				<span class="flex-none text-[13px] text-muted-foreground tabular-nums">
					{@render meta()}
				</span>
			{/if}
		</div>
		{#if actions}
			<div class="ml-auto flex flex-none items-center gap-2">{@render actions()}</div>
		{/if}
	</div>
	{#if description}
		<p class="mt-1 text-muted-foreground">{@render description()}</p>
	{/if}
</div>
