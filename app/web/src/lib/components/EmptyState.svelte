<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { ClassValue } from 'svelte/elements';

	interface Props {
		/**
		 * `onboarding`: what to do on a page with nothing to show yet (no runs, datasets or
		 * models, none matching), a centred column of a fixed width. `compact`: a line where a tab
		 * or pane has nothing to show, on the page's gutters, in body type.
		 */
		variant?: 'onboarding' | 'compact';
		/** The onboarding title, such as "No runs yet". */
		title?: string;
		/** One short line under the onboarding title. */
		lead?: Snippet;
		/** Under the lead: a code sample (`CopyBlock`) or steps, at the column's full width. */
		children?: Snippet;
		/** Under everything, centred and quieter: a secondary link or a button or two. */
		actions?: Snippet;
		/** A compact one is muted, or says something went wrong. */
		tone?: 'muted' | 'error';
		class?: ClassValue;
	}

	let {
		variant = 'compact',
		title,
		lead,
		children,
		actions,
		tone = 'muted',
		class: className
	}: Props = $props();
</script>

{#if variant === 'onboarding'}
	<!-- One look for every empty page: centred, the same width whatever the window. -->
	<div class={['px-5 pt-[12vh] pb-16', className]}>
		<div class="mx-auto w-full max-w-[36rem]">
			<div class="text-center">
				<h2 class="text-[20px] leading-[1.2] font-medium tracking-[-0.01em] text-balance">
					{title}
				</h2>
				{#if lead}
					<p
						class="mx-auto mt-2 max-w-[30rem] text-[14px] leading-[1.45] text-balance text-muted-foreground"
					>
						{@render lead()}
					</p>
				{/if}
			</div>
			{#if children}
				<div class="mt-7 text-left">{@render children()}</div>
			{/if}
			{#if actions}
				<div
					class="mt-6 flex flex-wrap items-center justify-center gap-2 text-center text-[13px] text-balance text-muted-foreground"
				>
					{@render actions()}
				</div>
			{/if}
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
