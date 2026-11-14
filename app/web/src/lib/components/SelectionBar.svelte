<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { Selection } from '#lib/selection.svelte.ts';

	interface Props {
		selection: Selection;
		/** The actions on the rows checked (Move, Delete). */
		children: Snippet;
	}

	let { selection, children }: Props = $props();
</script>

<svelte:window onkeydown={selection.onkeydown} />

<!-- In place of a list's header buttons while rows are checked (D130). -->
<div class="flex items-center gap-1" role="toolbar" aria-label="Selection">
	<span class="mr-2 text-[13px] tabular-nums">
		{selection.size.toLocaleString('en-US')} selected
	</span>
	{@render children()}
	<Button variant="ghost" size="sm" class="text-muted-foreground" onclick={selection.clear}>
		Clear
	</Button>
</div>
