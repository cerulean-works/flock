<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { ClassValue } from 'svelte/elements';

	interface Props {
		/**
		 * `onboarding`: what to do on a page with nothing to show yet (no runs, none matching), a
		 * reading column under a large title. `compact`: a line where a tab or pane has nothing
		 * to show, on the page's gutters, in body type.
		 */
		variant?: 'onboarding' | 'compact';
		/** The onboarding title. */
		title?: string;
		/** Under an onboarding title, at reading size. */
		lead?: Snippet;
		/** A compact one is muted, or says something went wrong. */
		tone?: 'muted' | 'error';
		class?: ClassValue;
		children?: Snippet;
	}

	let {
		variant = 'compact',
		title,
		lead,
		tone = 'muted',
		class: className,
		children
	}: Props = $props();
</script>

{#if variant === 'onboarding'}
	<div class={['px-5 pt-[10vh] pb-16', className]}>
		<div class="mx-auto max-w-160">
			<h2 class="text-[28px] leading-tight tracking-[-0.02em]">{title}</h2>
			{#if lead}
				<div class="mt-3 text-[17px] leading-[1.45]">{@render lead()}</div>
			{/if}
			{@render children?.()}
		</div>
	</div>
{:else}
	<div
		class={[
			'px-5 py-6',
			tone === 'error' ? 'text-destructive' : 'text-muted-foreground',
			className
		]}
		role={tone === 'error' ? 'alert' : undefined}
	>
		{@render children?.()}
	</div>
{/if}
