<script lang="ts" generics="T">
	import CheckIcon from '@lucide/svelte/icons/check';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import type { Snippet } from 'svelte';

	/**
	 * A picker's list, worked from its search box: ↑↓ move and wrap, Enter picks the active option,
	 * Backspace in an empty box goes up a level and Escape goes back one. The popover pickers and the
	 * inline folder panel all list through it, so they read and answer keys the same way.
	 */
	interface Props {
		/** The options shown, already narrowed by the query. */
		options: readonly T[];
		/** Each option's key, unique in the list. */
		key: (option: T) => string;
		/** An option's content, after its check mark. */
		row: Snippet<[T]>;
		/** Whether an option is the value now, which gets a check mark. No marks when omitted. */
		current?: (option: T) => boolean;
		/** What the options are, for the list's name and "No datasets match.". */
		noun: string;
		/** The search box's name, for screen readers. */
		label: string;
		placeholder: string;
		query?: string;
		/** The option the keys are on, which Enter picks. */
		active?: number;
		loading?: boolean;
		error?: string | null;
		/** The search box's id, for a label outside. */
		id?: string;
		disabled?: boolean;
		/** The scroll area's height. */
		class?: string;
		onpick: (option: T) => void;
		/** Backspace in an empty box: up a folder. The key does nothing when omitted. */
		onup?: () => void;
		/** Escape: back to the level left, instead of closing. Closes as usual when omitted. */
		onback?: () => void;
		/** Under the list, above the keys: "Show more", "New folder". */
		actions?: Snippet;
	}

	let {
		options,
		key,
		row,
		current,
		noun,
		label,
		placeholder,
		query = $bindable(''),
		active = $bindable(0),
		loading = false,
		error = null,
		id,
		disabled = false,
		class: height = 'max-h-72',
		onpick,
		onup,
		onback,
		actions
	}: Props = $props();

	const uid = $props.id();

	function onkeydown(e: KeyboardEvent) {
		// An input method's keys pick its own candidate, as in Autocomplete.
		if (e.isComposing) return;
		const count = options.length;
		if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
			e.preventDefault();
			if (count) active = (active + (e.key === 'ArrowDown' ? 1 : count - 1)) % count;
		} else if (e.key === 'Enter') {
			// Never the submit of a dialog's form around the picker.
			e.preventDefault();
			if (options[active] !== undefined) onpick(options[active]);
		} else if (e.key === 'Backspace' && !query && onup) {
			e.preventDefault();
			onup();
		} else if (e.key === 'Escape' && onback) {
			// Before the popover or dialog around it sees the key and closes.
			e.preventDefault();
			e.stopPropagation();
			onback();
		}
	}

	// The active option stays in view as the arrows move it.
	$effect(() => {
		document.getElementById(`${uid}-${active}`)?.scrollIntoView({ block: 'nearest' });
	});
</script>

<div class="border-b border-divider px-2 py-1.5">
	<input
		{id}
		{disabled}
		role="combobox"
		aria-expanded="true"
		aria-controls="{uid}-list"
		aria-activedescendant={options[active] !== undefined ? `${uid}-${active}` : undefined}
		aria-label={label}
		{placeholder}
		class="h-7 w-full min-w-0 bg-transparent text-[13px] outline-none placeholder:text-faint disabled:opacity-50"
		spellcheck="false"
		autocomplete="off"
		bind:value={query}
		oninput={() => (active = 0)}
		{onkeydown}
	/>
</div>
<div class={['overflow-y-auto p-1 text-[13px]', height]}>
	<div id="{uid}-list" role="listbox" aria-label={noun}>
		{#each options as option, i (key(option))}
			<!-- The keys are the search box's; a press keeps the focus there. -->
			<!-- svelte-ignore a11y_click_events_have_key_events -->
			<div
				id="{uid}-{i}"
				role="option"
				tabindex="-1"
				aria-selected={i === active}
				aria-disabled={disabled}
				class="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 aria-disabled:pointer-events-none aria-disabled:opacity-50 aria-selected:bg-accent"
				onmousedown={(e) => e.preventDefault()}
				onmousemove={() => (active = i)}
				onclick={() => onpick(option)}
			>
				{#if current}
					<CheckIcon class={['size-3.5 flex-none', !current(option) && 'invisible']} />
				{/if}
				{@render row(option)}
			</div>
		{/each}
	</div>
	{#if error}
		<p class="px-2 py-2 text-xs text-destructive" role="alert">{error}</p>
	{:else if loading}
		<p class="flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground">
			<LoaderCircleIcon class="size-3.5 animate-spin" /> Loading…
		</p>
	{:else if !options.length}
		<p class="px-2 py-2 text-xs text-muted-foreground">
			{`No ${noun} ${query.trim() ? 'match' : 'here'}.`}
		</p>
	{/if}
</div>
{@render actions?.()}
