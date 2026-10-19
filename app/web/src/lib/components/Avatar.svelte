<script lang="ts">
	import type { ClassValue } from 'svelte/elements';
	import { cn } from '#lib/utils.ts';

	interface Props {
		name: string;
		/** The picture (`avatar_url`); initials without one, or if it fails to load. */
		src?: string | null;
		/** Merged over the defaults, so a size or text class given here replaces theirs. */
		class?: ClassValue;
	}

	let { name, src = null, class: className }: Props = $props();

	const initials = $derived(
		name
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((w) => w[0].toUpperCase())
			.join('') || '?'
	);

	// Remembers which picture failed, so a new one is tried again.
	let broken = $state<string | null>(null);
	const shown = $derived(src && src !== broken ? src : null);
</script>

<span
	class={cn(
		'grid size-7 flex-none place-items-center overflow-hidden rounded-full bg-deep text-[11px] font-medium text-[#fffdf4]',
		className
	)}
	aria-hidden="true"
>
	{#if shown}
		<img
			src={shown}
			alt=""
			class="size-full object-cover"
			decoding="async"
			onerror={() => (broken = shown)}
		/>
	{:else}
		{initials}
	{/if}
</span>
