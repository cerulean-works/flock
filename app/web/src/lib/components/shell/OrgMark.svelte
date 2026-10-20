<script lang="ts">
	import type { ClassValue } from 'svelte/elements';
	import { cn } from '#lib/utils.ts';

	interface Props {
		name: string;
		/** The organization's picture (`avatar_url`); its initial without one. */
		src?: string | null;
		/** Merged over the defaults, so a size or text class given here replaces theirs. */
		class?: ClassValue;
	}

	let { name, src = null, class: className }: Props = $props();

	let broken = $state<string | null>(null);
	const shown = $derived(src && src !== broken ? src : null);
</script>

<!-- An organization's picture or initial: square, where a person's avatar is round. The tile
     colour is for the initial only: under a picture it would show through its transparent parts
     and, at the rounded corners, as a dark hairline where the clipped edge is antialiased. The
     picture takes the tile's rounding itself instead (whatever size or radius a caller gives).
     Evening's --deep is nearly its background, so the tile is the selection blue there. -->
<span
	class={cn(
		'grid size-7 flex-none place-items-center rounded-md font-serif text-[14px] text-[#fffdf4]',
		!shown && 'bg-deep dark:bg-selection',
		className
	)}
	aria-hidden="true"
>
	{#if shown}
		<img
			src={shown}
			alt=""
			class="size-full rounded-[inherit] object-cover"
			decoding="async"
			onerror={() => (broken = shown)}
		/>
	{:else}
		{name.charAt(0).toUpperCase()}
	{/if}
</span>
