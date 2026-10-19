<script lang="ts">
	import './layout.css';
	import touchIcon from '#lib/assets/apple-touch-icon.png';
	import favicon from '#lib/assets/favicon.png';
	import { Toaster } from '#lib/components/ui/sonner/index.js';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();
</script>

<svelte:head>
	<!-- /favicon.ico (static) is for browsers and tools that skip the link tags. -->
	<link rel="icon" href="/favicon.ico" sizes="32x32" />
	<link rel="icon" href={favicon} type="image/png" sizes="64x64" />
	<link rel="apple-touch-icon" href={touchIcon} />
</svelte:head>

{@render children()}

<Toaster position="bottom-right" />
