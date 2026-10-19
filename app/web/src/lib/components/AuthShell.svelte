<script lang="ts">
	import type { Snippet } from 'svelte';
	import sky from '#lib/assets/brand/sky.jpg';
	import LogoMark from './LogoMark.svelte';

	let { children }: { children: Snippet } = $props();
</script>

<svelte:head>
	<link rel="preload" as="image" href={sky} fetchpriority="high" />
</svelte:head>

<!-- The signed-out pages: the sky beside a narrow form column. -->
<div class="grid min-h-dvh md:grid-cols-[minmax(0,1.15fr)_minmax(420px,1fr)]">
	<section class="relative hidden overflow-hidden bg-deep md:block" aria-hidden="true">
		<img src={sky} alt="" class="absolute inset-0 size-full object-cover" decoding="async" />
	</section>

	<main class="flex items-center justify-center overflow-y-auto px-6 py-16">
		<div class="w-full max-w-90">
			<LogoMark class="mb-8 size-8 md:hidden" />
			{@render children()}
		</div>
	</main>
</div>
