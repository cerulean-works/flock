<script lang="ts" module>
	export interface AutocompleteOption {
		value: string;
		/** Shown muted after the value, e.g. a column's kind. */
		hint?: string;
	}
</script>

<script lang="ts">
	import { Input } from '#lib/components/ui/input/index.js';

	// A text input suggesting values: its list opens on focus with every option, narrows as you
	// type, and is exactly as wide as the input. Free text is fine; the options only help.
	// Escape closes the list alone, so a popover holding the input stays open.

	interface Props {
		value: string;
		options: readonly AutocompleteOption[];
		/** Called as the value is typed or picked. */
		onValueChange?: (value: string) => void;
		/** Options in a monospace font, like column names. */
		mono?: boolean;
		placeholder?: string;
		disabled?: boolean;
		class?: string;
		'aria-label'?: string;
	}

	let {
		value = $bindable(''),
		options,
		onValueChange,
		mono = false,
		placeholder,
		disabled = false,
		class: className,
		'aria-label': ariaLabel
	}: Props = $props();

	const uid = $props.id();
	/** Options listed at once; more show as the text narrows them. */
	const SHOWN = 200;

	let open = $state(false);
	/** Whether the text was typed since the list opened: only typed text narrows it. */
	let typed = $state(false);
	/** The highlighted option's index, -1 for none. */
	let active = $state(-1);
	let list = $state<HTMLElement | null>(null);

	/** Options containing the typed text: the text itself first, then those starting with it. */
	const matches = $derived.by(() => {
		const q = typed ? value.trim().toLowerCase() : '';
		if (!q) return options.slice(0, SHOWN);
		const exact: AutocompleteOption[] = [];
		const starts: AutocompleteOption[] = [];
		const within: AutocompleteOption[] = [];
		for (const o of options) {
			const v = o.value.toLowerCase();
			if (v === q) exact.push(o);
			else if (v.startsWith(q)) starts.push(o);
			else if (v.includes(q)) within.push(o);
			if (exact.length + starts.length >= SHOWN) break;
		}
		return [...exact, ...starts, ...within].slice(0, SHOWN);
	});

	const shown = $derived(open && !disabled && matches.length > 0);

	/** Opens with every option, the current value highlighted. */
	function show() {
		if (open || disabled) return;
		typed = false;
		open = true;
		active = options.findIndex((o) => o.value === value);
		if (active >= SHOWN) active = -1;
	}

	function pick(o: AutocompleteOption) {
		value = o.value;
		onValueChange?.(o.value);
		open = false;
	}

	function move(by: number) {
		if (!shown) return show();
		const n = matches.length;
		active = active < 0 ? (by > 0 ? 0 : n - 1) : (active + by + n) % n;
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.isComposing) return;
		switch (e.key) {
			case 'ArrowDown':
			case 'ArrowUp':
				e.preventDefault();
				move(e.key === 'ArrowDown' ? 1 : -1);
				break;
			case 'Enter':
				// A highlighted option is picked; otherwise Enter submits the form as usual.
				if (shown && matches[active]) {
					e.preventDefault();
					pick(matches[active]);
				} else open = false;
				break;
			case 'Escape':
				if (shown) {
					e.preventDefault();
					e.stopPropagation(); // keeps an enclosing popover or dialog open
					open = false;
				}
				break;
			case 'Tab':
				open = false;
				break;
		}
	}

	// Keeps the highlighted option in view while moving through a long list.
	$effect(() => {
		if (shown && active >= 0)
			list?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' });
	});
</script>

<div class="relative min-w-0">
	<Input
		{value}
		{placeholder}
		{disabled}
		class={className}
		aria-label={ariaLabel}
		role="combobox"
		aria-expanded={shown}
		aria-controls="{uid}-list"
		aria-autocomplete="list"
		aria-activedescendant={shown && active >= 0 ? `${uid}-${active}` : undefined}
		autocomplete="off"
		spellcheck="false"
		onfocus={show}
		onclick={show}
		onblur={() => (open = false)}
		oninput={(e) => {
			value = e.currentTarget.value;
			onValueChange?.(value);
			typed = true;
			open = true;
			active = value.trim() ? 0 : -1;
		}}
		{onkeydown}
	/>
	{#if shown}
		<!-- Clicks keep focus in the input; keys reach the list through it. -->
		<div
			bind:this={list}
			id="{uid}-list"
			role="listbox"
			tabindex="-1"
			class="absolute inset-x-0 top-full z-50 mt-1 max-h-60 overflow-y-auto rounded-lg bg-popover p-1 text-popover-foreground shadow-md ring-1 ring-foreground/10"
			onpointerdown={(e) => e.preventDefault()}
		>
			{#each matches as o, i (o.value)}
				<!-- svelte-ignore a11y_click_events_have_key_events (keys reach options via the input) -->
				<div
					id="{uid}-{i}"
					data-index={i}
					role="option"
					tabindex="-1"
					aria-selected={i === active}
					class="flex cursor-default items-center gap-2 rounded-md px-1.5 py-1 text-sm select-none aria-selected:bg-accent aria-selected:text-accent-foreground"
					onpointermove={() => (active = i)}
					onclick={() => pick(o)}
				>
					<span
						class={['min-w-0 flex-1 truncate', mono && 'font-mono text-[12.5px]']}
						title={o.value}
					>
						{o.value}
					</span>
					{#if o.hint}
						<span class="shrink-0 text-xs text-muted-foreground">{o.hint}</span>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
