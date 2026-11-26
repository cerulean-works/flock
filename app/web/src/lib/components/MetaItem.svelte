<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import { hint } from '#lib/hint.svelte.ts';
	import { cn } from '#lib/utils.js';

	interface Props {
		/** A lucide icon drawn small before the text. */
		icon?: Component<{ class?: string }>;
		/** Makes the item a link. */
		href?: string;
		/** Hover text. */
		title?: string;
		/** Monospace, for IDs and other machine names. */
		mono?: boolean;
		/** A quiet chip, for a role or a status rather than a fact. */
		chip?: boolean;
		class?: string;
		children: Snippet;
	}

	let { icon: Icon, href, title, mono, chip, class: className, children }: Props = $props();
</script>

<!-- One fact in a MetaRow. -->
<svelte:element
	this={href ? 'a' : 'span'}
	{href}
	{@attach hint(title)}
	class={cn(
		'inline-flex max-w-full min-w-0 items-center gap-1.5 tabular-nums',
		mono && 'font-mono text-[12px] text-foreground/80',
		chip && 'h-5 rounded-full bg-muted px-2 text-[12px] font-medium text-foreground',
		href && 'hover:text-link',
		className
	)}
>
	{#if Icon}<Icon class="size-3.5 flex-none text-faint" />{/if}
	<span class="truncate">{@render children()}</span>
</svelte:element>
