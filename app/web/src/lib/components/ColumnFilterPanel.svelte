<script lang="ts">
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import { onDestroy } from 'svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import MenuSearch from '#lib/components/MenuSearch.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { FilterValue } from './ColumnFilter.svelte';

	interface Props {
		label: string;
		active: boolean;
		kind?: 'values' | 'range' | 'text';
		values?: FilterValue[];
		selected?: readonly string[];
		onselect?: (selected: string[]) => void;
		range?: { min?: string; max?: string };
		onrange?: (range: { min?: string; max?: string }) => void;
		date?: boolean;
		text?: string;
		ontext?: (text: string) => void;
		onclear: () => void;
		/** Closes whatever holds the panel (Enter in a field, Clear). */
		onclose: () => void;
		/** Back to a list of columns, when the panel is one step of it. */
		onback?: () => void;
	}

	let {
		label,
		active,
		kind = 'values',
		values = [],
		selected = [],
		onselect,
		range = {},
		onrange,
		date = false,
		text = '',
		ontext,
		onclear,
		onclose,
		onback
	}: Props = $props();

	// Edits are drafts, applied when the panel goes (closed, or back to the columns): applying
	// reloads the runs under it, so ticking several values needs the list to stay.
	// svelte-ignore state_referenced_locally
	let draft = $state<string[]>([...selected]);
	// svelte-ignore state_referenced_locally
	let draftRange = $state({ ...range });
	// svelte-ignore state_referenced_locally
	let draftText = $state(text);
	let query = $state('');

	const chosen = $derived(new Set(draft));
	// Values chosen stay listed, even when no run has them now.
	const options: FilterValue[] = $derived([
		...values,
		...[...new Set([...selected, ...draft])]
			.filter((v) => !values.some((o) => o.value === v))
			.map((v) => ({ value: v, label: v, count: 0 }))
	]);
	const q = $derived(query.trim().toLowerCase());
	const shown = $derived(q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options);

	const same = (a: readonly string[], b: readonly string[]) =>
		a.length === b.length && a.every((v) => b.includes(v));

	function toggle(value: string) {
		draft = chosen.has(value) ? draft.filter((v) => v !== value) : [...draft, value];
	}

	function apply() {
		const empty =
			kind === 'values'
				? !draft.length
				: kind === 'range'
					? !draftRange.min?.trim() && !draftRange.max?.trim()
					: !draftText.trim();
		if (empty) {
			if (active) onclear();
			return;
		}
		if (kind === 'values' && !same(draft, selected)) onselect?.(draft);
		else if (kind === 'range' && (draftRange.min !== range.min || draftRange.max !== range.max))
			onrange?.(draftRange);
		else if (kind === 'text' && draftText.trim() !== text) ontext?.(draftText.trim());
	}
	// Applied after the panel is gone: navigating from inside its teardown would write state
	// mid-update.
	onDestroy(() => queueMicrotask(apply));

	function clear() {
		draft = [];
		draftRange = {};
		draftText = '';
		onclose();
	}

	function enter(e: KeyboardEvent) {
		if (e.key !== 'Enter') return;
		e.preventDefault();
		onclose();
	}

	const FIELD =
		'h-7 w-full min-w-0 rounded-md bg-muted/60 px-2 text-[13px] text-foreground outline-none placeholder:text-faint focus-visible:ring-3 focus-visible:ring-ring/50';
</script>

<div class={['flex h-9 items-center justify-between pr-1.5', onback ? 'pl-1' : 'pl-3']}>
	<span class="flex min-w-0 items-center gap-1">
		{#if onback}
			<Button variant="quiet" size="icon-bar" aria-label="Back to the columns" onclick={onback}>
				<ArrowLeftIcon />
			</Button>
		{/if}
		<span class="truncate font-medium">Filter: {label}</span>
	</span>
	{#if active}
		<Button variant="quiet" size="bar" onclick={clear}>Clear</Button>
	{/if}
</div>
{#if kind === 'values'}
	{#if options.length > 8}
		<MenuSearch
			class="border-t"
			placeholder="Search values…"
			label="Search {label.toLowerCase()} values"
			bind:value={query}
			{onback}
			autofocus
		/>
	{/if}
	<div class={['px-1.5 pb-1.5', options.length > 8 && 'pt-1.5']}>
		<div class="max-h-64 overflow-y-auto" role="group" aria-label="{label} values">
			{#each shown as o (o.value)}
				<label class="flex h-8 cursor-pointer items-center gap-2 rounded-md px-1.5 hover:bg-muted">
					<input
						type="checkbox"
						class="size-3.5 flex-none accent-(--primary)"
						checked={chosen.has(o.value)}
						onchange={() => toggle(o.value)}
					/>
					{#if o.dot}
						<span class={['size-2 flex-none rounded-full', o.dot]} aria-hidden="true"></span>
					{/if}
					{#if o.person}
						<Avatar
							name={o.person.name}
							src={o.person.avatar_url}
							class="size-4 flex-none text-[8px]"
						/>
					{/if}
					<span class="min-w-0 flex-1 truncate">{o.label}</span>
					{#if o.count !== undefined}
						<span class="text-xs text-muted-foreground tabular-nums">{o.count}</span>
					{/if}
				</label>
			{:else}
				<p class="px-1.5 py-2 text-center text-xs text-muted-foreground">No values</p>
			{/each}
		</div>
	</div>
	<div class="flex justify-between border-t border-divider p-1">
		<Button
			variant="quiet"
			size="bar"
			disabled={!shown.length}
			onclick={() => (draft = [...new Set([...draft, ...shown.map((o) => o.value)])])}
		>
			Select {q ? 'matching' : 'all'}
		</Button>
		<Button variant="quiet" size="bar" disabled={!draft.length} onclick={() => (draft = [])}>
			Clear
		</Button>
	</div>
{:else if kind === 'range'}
	<div class="grid grid-cols-2 gap-2 px-3 pb-3">
		{#each ['min', 'max'] as const as k, i (k)}
			<label class="grid gap-1 text-xs text-muted-foreground">
				{k === 'min' ? (date ? 'From' : 'At least') : date ? 'To' : 'At most'}
				<!-- svelte-ignore a11y_autofocus -->
				<input
					class={FIELD}
					type={date ? 'datetime-local' : 'text'}
					inputmode={date ? undefined : 'decimal'}
					bind:value={draftRange[k]}
					autofocus={i === 0}
					onkeydown={enter}
				/>
			</label>
		{/each}
	</div>
{:else}
	<div class="px-3 pb-3">
		<!-- svelte-ignore a11y_autofocus -->
		<input
			class={FIELD}
			placeholder="Contains…"
			aria-label="{label} contains"
			bind:value={draftText}
			autofocus
			onkeydown={enter}
		/>
	</div>
{/if}
