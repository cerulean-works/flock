<script lang="ts">
	import InfoIcon from '@lucide/svelte/icons/info';
	import type { Snippet } from 'svelte';

	/**
	 * A setting's explanation folded behind an info icon beside its label: shown while the icon
	 * is hovered or focused, so a form reads as its controls first.
	 */
	let { label, children }: { label: string; children: Snippet } = $props();

	const id = $props.id();
</script>

<span class="group/hint relative inline-flex">
	<button
		type="button"
		class="rounded-full text-faint outline-none hover:text-foreground focus-visible:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
		aria-label={label}
		aria-describedby={id}
	>
		<InfoIcon class="size-3.5" />
	</button>
	<span
		{id}
		role="tooltip"
		class="invisible absolute top-full left-1/2 z-50 mt-1.5 w-60 -translate-x-1/2 rounded-md border bg-popover px-2.5 py-2 text-xs leading-snug font-normal text-popover-foreground opacity-0 shadow-md transition-opacity group-focus-within/hint:visible group-focus-within/hint:opacity-100 group-hover/hint:visible group-hover/hint:opacity-100"
	>
		{@render children()}
	</span>
</span>
