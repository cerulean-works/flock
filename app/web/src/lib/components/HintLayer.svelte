<script lang="ts">
	import { tip } from '#lib/hint.svelte.ts';

	// The page's one hover hint (#lib/hint.svelte.ts): its bottom corner just off the pointer, up
	// and to the right, flipped left near the window's right edge and down near its top.
	let width = $state(0);
	let height = $state(0);
	let innerWidth = $state(0);

	const flipX = $derived(tip.x + 12 + width > innerWidth - 8);
	const flipY = $derived(tip.y - 12 - height < 8);
</script>

<svelte:window bind:innerWidth />

{#if tip.owner}
	<div
		aria-hidden="true"
		class="pointer-events-none fixed z-60 max-w-80 rounded-md border bg-popover px-2 py-1 text-xs leading-snug break-words whitespace-pre-line text-popover-foreground shadow-md"
		style:left={flipX ? null : `${tip.x + 12}px`}
		style:right={flipX ? `${innerWidth - tip.x + 12}px` : null}
		style:top={flipY ? `${tip.y + 16}px` : null}
		style:bottom={flipY ? null : `calc(100% - ${tip.y - 12}px)`}
		bind:clientWidth={width}
		bind:clientHeight={height}
	>
		{tip.text.length > 600 ? `${tip.text.slice(0, 600)}…` : tip.text}
	</div>
{/if}
