<script lang="ts">
	import type { ClassValue } from 'svelte/elements';

	interface Props {
		name: string;
		/** The organization's picture (`avatar_url`); its initial without one. */
		src?: string | null;
		class?: ClassValue;
	}

	let { name, src = null, class: className }: Props = $props();

	let broken = $state<string | null>(null);
	const shown = $derived(src && src !== broken ? src : null);
</script>

<!-- An organization's picture or initial: square, where a person's avatar is round. -->
<span
	class={[
		'grid size-7 flex-none place-items-center overflow-hidden rounded-md bg-deep font-serif text-[14px] text-[#fffdf4]',
		className
	]}
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
		{name.charAt(0).toUpperCase()}
	{/if}
</span>
