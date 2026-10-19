<script lang="ts">
	import type { ResolvedPathname } from '$app/types';
	import { tabsListVariants, tabsTriggerClass } from '#lib/components/ui/tabs/index.js';
	import { cn } from '#lib/utils.ts';

	interface Props {
		/** What the choice is about, for screen readers. */
		label: string;
		/** One link per choice; `current` is the one shown. */
		items: readonly { href: ResolvedPathname; label: string; current: boolean }[];
		class?: string;
	}

	let { label, items, class: className }: Props = $props();
</script>

<!-- Switches a view by its URL, looking like the workspace's view tabs. Each choice replaces the
     history entry and keeps the scroll, as a tab would. -->
<nav class={cn(tabsListVariants(), 'h-8', className)} data-variant="default" aria-label={label}>
	{#each items as item (item.href)}
		<a
			class={cn(tabsTriggerClass)}
			href={item.href}
			data-sveltekit-noscroll
			data-sveltekit-replacestate
			data-active={item.current || undefined}
			aria-current={item.current ? 'page' : undefined}
		>
			{item.label}
		</a>
	{/each}
</nav>
