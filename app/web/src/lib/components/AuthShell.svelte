<script lang="ts">
	import type { Snippet } from 'svelte';
	import skyDay from '#lib/assets/brand/sky-day.webp';
	import skyNight from '#lib/assets/brand/sky-night.webp';
	import LogoMark from './LogoMark.svelte';

	let { children }: { children: Snippet } = $props();
</script>

<!-- The signed-out pages: a form card over the sky, by day or by night with the theme. As
     backgrounds, only the one the theme shows is downloaded. -->
<main
	class="relative flex h-full items-center justify-center overflow-y-auto bg-deep bg-(image:--sky-day) bg-cover bg-center px-4 py-16 dark:bg-(image:--sky-night)"
	style:--sky-day="url({skyDay})"
	style:--sky-night="url({skyNight})"
>
	<div
		class="w-full max-w-100 rounded-xl bg-background px-8 py-10 shadow-2xl ring-1 ring-foreground/10"
	>
		<LogoMark class="mb-8 size-8" />
		{@render children()}
	</div>
</main>
