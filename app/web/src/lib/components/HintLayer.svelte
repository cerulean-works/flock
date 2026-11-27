<script lang="ts">
	import Keys from '#lib/components/Keys.svelte';
	import { tip } from '#lib/hint.svelte.ts';

	// The page's one hover hint (#lib/hint.svelte.ts): its bottom corner just off the pointer, up
	// and to the right, flipped left near the window's right edge and down near its top.
	let width = $state(0);
	let height = $state(0);
	let innerWidth = $state(0);

	// "Label  Keys": what's after the two spaces on the first line is a shortcut, drawn as keys
	// ("G then R"); any further lines follow under it.
	const parts = $derived.by(() => {
		const [line, ...rest] = tip.text.split('\n');
		const plain = { label: tip.text, keys: [] as string[], more: '' };
		const at = line.lastIndexOf('  ');
		if (at < 0) return plain;
		const keys = line
			.slice(at + 2)
			.trim()
			.split(/\s+/);
		// Only short keys: a name or description with two spaces in it stays text.
		if (keys.length > 3 || keys.some((k) => k.length > 4)) return plain;
		return { label: line.slice(0, at), keys, more: rest.join('\n') };
	});

	// Under a focused element it starts at its left edge, just below it.
	const gap = $derived(tip.below ? 0 : 12);
	const flipX = $derived(tip.x + gap + width > innerWidth - 8);
	const flipY = $derived(tip.below || tip.y - 12 - height < 8);
</script>

<svelte:window bind:innerWidth />

{#if tip.owner}
	<div
		aria-hidden="true"
		class="pointer-events-none fixed z-60 max-w-80 rounded-md border bg-popover px-2 py-1 text-xs leading-snug break-words whitespace-pre-line text-popover-foreground shadow-md"
		style:left={flipX ? null : `${tip.x + gap}px`}
		style:right={flipX ? `${innerWidth - tip.x + gap}px` : null}
		style:top={flipY ? `${tip.y + (tip.below ? 0 : 16)}px` : null}
		style:bottom={flipY ? null : `calc(100% - ${tip.y - 12}px)`}
		bind:clientWidth={width}
		bind:clientHeight={height}
	>
		{#if parts.keys.length}
			<span class="flex items-center gap-2">
				{parts.label}
				<Keys keys={parts.keys.join(' ')} />
			</span>
			{#if parts.more}<span class="block text-muted-foreground">{parts.more}</span>{/if}
		{:else}
			{tip.text.length > 600 ? `${tip.text.slice(0, 600)}…` : tip.text}
		{/if}
	</div>
{/if}
