<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import type { Snippet } from 'svelte';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import { cn } from '#lib/utils.ts';

	/**
	 * A picker's field: a select-like button showing the value, which opens its list in a popover.
	 * The whole field is the trigger, so it reads as changeable on sight.
	 */
	interface Props {
		open?: boolean;
		/** The field's content: the value now. */
		children: Snippet;
		/** The popover's content: a `PickerList`, with anything it browses around it. */
		list: Snippet;
		id?: string;
		disabled?: boolean;
		'aria-label'?: string;
		/** The field's size, over the default full-width one. */
		class?: string;
		onOpenChange?: (open: boolean) => void;
	}

	let {
		open = $bindable(false),
		children,
		list,
		id,
		disabled = false,
		'aria-label': ariaLabel,
		class: className,
		onOpenChange
	}: Props = $props();

	let content = $state<HTMLElement | null>(null);
</script>

<Popover.Root bind:open {onOpenChange}>
	<Popover.Trigger {disabled}>
		{#snippet child({ props })}
			<button
				{...props}
				{id}
				type="button"
				aria-label={ariaLabel}
				class={cn(
					'group/field flex h-8 w-full min-w-0 items-center gap-2 rounded-lg border border-input bg-transparent px-2.5 text-left text-sm transition-colors outline-none hover:bg-muted/60 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 data-[state=open]:border-ring dark:bg-input/30',
					className
				)}
			>
				{@render children()}
				<ChevronDownIcon
					class="size-4 flex-none text-muted-foreground transition-transform group-hover/field:text-foreground group-data-[state=open]/field:rotate-180"
				/>
			</button>
		{/snippet}
	</Popover.Trigger>
	<!-- Opening focuses the search box, even when a button (the dataset picker's folder trail)
	     comes before it: typing filters at once. -->
	<Popover.Content
		bind:ref={content}
		align="start"
		class="w-(--bits-popover-anchor-width) max-w-[calc(100vw-2rem)] min-w-80 gap-0 p-0"
		onOpenAutoFocus={(e) => {
			e.preventDefault();
			content?.querySelector<HTMLElement>('[role=combobox]')?.focus();
		}}
	>
		{@render list()}
	</Popover.Content>
</Popover.Root>
