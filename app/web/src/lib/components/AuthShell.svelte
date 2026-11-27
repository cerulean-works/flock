<script lang="ts">
	import type { Snippet } from 'svelte';
	import skyDay from '#lib/assets/brand/sky-day.webp';
	import skyNight from '#lib/assets/brand/sky-night.webp';
	import LogoMark from './LogoMark.svelte';

	let {
		title,
		mark,
		children
	}: {
		/** The page's one heading, centred under the mark. */
		title: string;
		/** Replaces the Flock mark (an invitation shows the org's). */
		mark?: Snippet;
		children?: Snippet;
	} = $props();
</script>

<!-- The signed-out pages: a form card over the sky, by day or by night with the theme. As
     backgrounds, only the one the theme shows is downloaded. A faint wash keeps the photo
     behind the card. -->
<main
	class="relative flex h-full items-center justify-center overflow-y-auto bg-deep bg-(image:--sky-day) bg-cover bg-center px-4 py-16 before:pointer-events-none before:fixed before:inset-0 before:bg-deep/15 dark:bg-(image:--sky-night) dark:before:bg-black/25"
	style:--sky-day="url({skyDay})"
	style:--sky-night="url({skyNight})"
>
	<div
		class="relative w-full max-w-90 rounded-2xl bg-background px-8 pt-10 pb-8 shadow-[0_1px_2px_rgb(0_0_0/0.08),0_24px_64px_-12px_rgb(0_0_0/0.4)] ring-1 ring-foreground/8"
	>
		<div class="flex justify-center">
			{#if mark}{@render mark()}{:else}<LogoMark class="size-8" />{/if}
		</div>
		<h1 class="mt-5 text-center text-[22px] leading-7 font-medium tracking-[-0.015em] text-balance">
			{title}
		</h1>
		{@render children?.()}
	</div>
</main>
