<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import SearchIcon from '@lucide/svelte/icons/search';
	import XIcon from '@lucide/svelte/icons/x';
	import { tick, type Snippet } from 'svelte';
	import { Button } from '#lib/components/ui/button/index.js';

	/**
	 * A search in a view bar: an icon until it's wanted, then a field that stays open while it has
	 * text or focus. Escape clears the text first, then closes it. `/` opens it from anywhere on
	 * the page outside a field. The one look for every search on a page (menus have their own).
	 */

	interface Props {
		value: string;
		/** What's searched, for the placeholder and labels: `runs` reads "Search runs…". */
		things: string;
		/** The placeholder when "Search <things>…" doesn't fit, e.g. "Search by prefix…". */
		placeholder?: string;
		/** The keys in the icon's hint; `/` by default. */
		keys?: string;
		/** `/` opens it; off where something else on the page owns `/`. */
		slash?: boolean;
		/** Always a field, never an icon (the field then takes its container's width). */
		open?: boolean;
		/** The × at the field's end once there's text. */
		clearable?: boolean;
		/** Open (bindable): the field is shown. */
		opened?: boolean;
		/** Classes for the field, e.g. a width on phones. */
		class?: string;
		oninput?: () => void;
		/** Runs first; preventing its default skips the field's own Escape handling. */
		onkeydown?: (e: KeyboardEvent) => void;
		/** After the × or Escape empties it. */
		onclear?: () => void;
		/** After it closes back to the icon; when `open`, after Escape or leaving it empty. */
		onclose?: () => void;
		/** Inside the field's end: a match count and its arrows. */
		trailing?: Snippet;
	}

	let {
		value = $bindable(),
		things,
		placeholder = `Search ${things}…`,
		keys = '/',
		slash = true,
		open = false,
		clearable = true,
		opened = $bindable(false),
		class: className = '',
		oninput,
		onkeydown,
		onclear,
		onclose,
		trailing
	}: Props = $props();

	const label = $derived(placeholder.replace(/…$/, ''));
	let input = $state<HTMLInputElement>();
	const shown = $derived(open || opened || !!value);

	/** Opens the field and puts the cursor in it, its text selected. */
	export async function focus() {
		opened = true;
		await tick();
		input?.focus();
		input?.select();
	}

	function clear() {
		value = '';
		oninput?.();
		onclear?.();
	}

	function close() {
		opened = false;
		input?.blur();
		onclose?.();
	}

	function keydown(e: KeyboardEvent) {
		onkeydown?.(e);
		if (e.defaultPrevented || e.key !== 'Escape') return;
		e.stopPropagation();
		if (value) clear();
		else close();
	}

	function onwindowkey(e: KeyboardEvent) {
		if (!slash || e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
		const t = e.target as HTMLElement;
		if (t.closest('input, textarea, select, [contenteditable], [role="dialog"], [role="menu"]'))
			return;
		e.preventDefault();
		void focus();
	}
</script>

<svelte:window onkeydown={onwindowkey} />

{#if !shown}
	<Button
		variant="quiet"
		size="icon-bar"
		aria-label={label}
		{@attach hint(keys ? `${label}  ${keys}` : label)}
		onclick={() => void focus()}
	>
		<SearchIcon />
	</Button>
{:else}
	<label
		class={[
			'flex h-7 min-w-0 items-center gap-1.5 rounded-md bg-muted pr-0.5 pl-2 focus-within:ring-3 focus-within:ring-ring/50',
			open ? 'w-full' : 'w-52',
			className
		]}
	>
		<SearchIcon class="size-3.5 flex-none text-muted-foreground" />
		<input
			bind:this={input}
			bind:value
			type="search"
			class="h-full min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint [&::-webkit-search-cancel-button]:hidden"
			{placeholder}
			aria-label={label}
			autocomplete="off"
			spellcheck="false"
			oninput={() => oninput?.()}
			onkeydown={keydown}
			onblur={() => {
				if (value || !(opened || open)) return;
				opened = false;
				onclose?.();
			}}
		/>
		{@render trailing?.()}
		{#if clearable && value}
			<button
				type="button"
				class="grid size-6 flex-none place-items-center rounded text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
				aria-label="Clear the search"
				onmousedown={(e) => e.preventDefault()}
				onclick={() => {
					clear();
					input?.focus();
				}}
			>
				<XIcon class="size-3.5" />
			</button>
		{/if}
	</label>
{/if}
