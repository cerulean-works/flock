<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';

	interface Props extends HTMLAttributes<HTMLDivElement> {
		/**
		 * `full`: the page spans the window and lays out its own header and scrolling (the runs
		 * pages). `home`: a scrolling page with its content centred at Home's width.
		 */
		width: 'full' | 'home';
		children: Snippet;
	}

	let { width, children, class: className, ...rest }: Props = $props();
</script>

<!--
	Every page of the app shell, at one of its two widths. A `home` page is a size container, so
	something in it can span the whole page with the `bleed` utility (the dataset row viewer).
-->
<div class={['flex min-h-0 flex-1 flex-col', className]} {...rest}>
	{#if width === 'home'}
		<main class="@container relative min-h-0 flex-1 overflow-y-auto px-5 pb-16">
			<div class="mx-auto max-w-280">{@render children()}</div>
		</main>
	{:else}
		{@render children()}
	{/if}
</div>
