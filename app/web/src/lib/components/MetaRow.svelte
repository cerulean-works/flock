<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '#lib/utils.js';

	let { class: className, children }: { class?: string; children: Snippet } = $props();
</script>

<!-- A line of facts about a thing (its ID, counts, your role), spaced apart instead of joined
     with middle dots. Fill it with MetaItem. -->
<div
	class={cn(
		'flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-muted-foreground',
		className
	)}
>
	{@render children()}
</div>
