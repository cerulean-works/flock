<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLInputAttributes } from 'svelte/elements';

	/**
	 * A popover's or menu's search: its top row, a bare 36px field over a divider, as Linear's
	 * menus open. Escape clears the text first; on an empty field it goes back a step (`onback`)
	 * or, without one, closes the popover around it as usual. Every picker, menu and panel that
	 * searches its list starts with this row, so they look and answer keys the same way.
	 */
	interface Props extends Omit<HTMLInputAttributes, 'value' | 'class'> {
		value?: string;
		ref?: HTMLInputElement | null;
		/** The field's name, for screen readers. */
		label: string;
		/** Escape on an empty field: back a step, instead of closing. */
		onback?: () => void;
		/** Before the field in the row: a back arrow, a scope chip. */
		before?: Snippet;
		/** The row's own classes. */
		class?: string;
	}

	let {
		value = $bindable(''),
		ref = $bindable(null),
		label,
		onback,
		before,
		class: row,
		onkeydown,
		...rest
	}: Props = $props();

	function keydown(e: KeyboardEvent & { currentTarget: EventTarget & HTMLInputElement }) {
		onkeydown?.(e);
		if (e.defaultPrevented || e.isComposing || e.key !== 'Escape') return;
		if (value) {
			// Before the popover or menu around it sees the key and closes.
			e.preventDefault();
			e.stopPropagation();
			value = '';
			e.currentTarget.value = '';
			e.currentTarget.dispatchEvent(new Event('input', { bubbles: true }));
		} else if (onback) {
			e.preventDefault();
			e.stopPropagation();
			onback();
		}
	}
</script>

<div class={['flex h-9 flex-none items-center gap-1.5 border-b border-divider px-3', row]}>
	{@render before?.()}
	<input
		bind:this={ref}
		bind:value
		aria-label={label}
		spellcheck="false"
		autocomplete="off"
		{...rest}
		class="h-full min-w-0 flex-1 bg-transparent text-[13px] text-foreground outline-none placeholder:text-faint disabled:opacity-50"
		onkeydown={keydown}
	/>
</div>
