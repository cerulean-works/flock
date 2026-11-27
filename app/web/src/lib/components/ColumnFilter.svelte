<script lang="ts" module>
	/** One value a column can be filtered to, with how many rows have it. */
	export interface FilterValue {
		value: string;
		label: string;
		count?: number;
		/** A person's face before the name (owners). */
		person?: { name: string; avatar_url: string | null };
		/** A state's dot colour class, as the State column draws it. */
		dot?: string;
	}
</script>

<script lang="ts">
	import FunnelIcon from '@lucide/svelte/icons/funnel';
	import { hint } from '#lib/hint.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';

	interface Props {
		/** The column's name: "Filter: Owner". */
		label: string;
		/** Whether the column filters the list now: the funnel stays, filled. */
		active: boolean;
		/**
		 * `values`: a checkbox list, several at once. `range`: at least / at most (numbers, or
		 * dates with `date`). `text`: contains.
		 */
		kind?: 'values' | 'range' | 'text';
		values?: FilterValue[];
		selected?: readonly string[];
		onselect?: (selected: string[]) => void;
		range?: { min?: string; max?: string };
		onrange?: (range: { min?: string; max?: string }) => void;
		/** A range of dates: From / To. */
		date?: boolean;
		text?: string;
		ontext?: (text: string) => void;
		onclear: () => void;
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
		onclear
	}: Props = $props();

	let open = $state(false);
	let query = $state('');
	// The values ticked while the list is open, applied when it closes: applying reloads the
	// list under it, so ticking several at once needs the list to stay.
	let draft = $state<string[]>([]);
	const chosen = $derived(new Set(draft));
	// Values chosen stay listed, even when no row has them now.
	const options: FilterValue[] = $derived([
		...values,
		...[...new Set([...selected, ...draft])]
			.filter((v) => !values.some((o) => o.value === v))
			.map((v) => ({ value: v, label: v, count: 0 }))
	]);
	const q = $derived(query.trim().toLowerCase());
	const shown = $derived(q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options);

	function toggle(value: string) {
		draft = chosen.has(value) ? draft.filter((v) => v !== value) : [...draft, value];
	}
	const same = (a: readonly string[], b: readonly string[]) =>
		a.length === b.length && a.every((v) => b.includes(v));

	const FIELD =
		'h-7 w-full min-w-0 rounded-md bg-muted/60 px-2 text-[13px] outline-none placeholder:text-faint focus-visible:ring-3 focus-visible:ring-ring/50';
</script>

<!--
	A column's filter, as the slurm dashboard draws it: a funnel beside the header's label that
	shows on hover and stays, filled, while the column filters the list; its choices in a popover.
	The header must be a `group/h`.
-->
<Popover.Root
	bind:open
	onOpenChange={(next) => {
		if (next) {
			query = '';
			draft = [...selected];
		} else if (kind === 'values' && !same(draft, selected)) onselect?.(draft);
	}}
>
	<Popover.Trigger>
		{#snippet child({ props })}
			<button
				{...props}
				type="button"
				class={[
					'relative z-2 grid size-5 flex-none place-items-center rounded outline-none hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:ring-3 focus-visible:ring-ring/50 data-[state=open]:bg-muted data-[state=open]:opacity-100',
					active ? 'text-primary' : 'opacity-0 group-hover/h:opacity-100 pointer-coarse:opacity-100'
				]}
				aria-label="Filter {label.toLowerCase()}"
				{@attach hint(`Filter ${label.toLowerCase()}`)}
			>
				<FunnelIcon class={['size-3', active && 'fill-current']} />
			</button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content align="start" class="w-64 gap-0 p-0 text-[13px] font-normal text-foreground">
		<div class="flex h-9 items-center justify-between pr-1.5 pl-3">
			<span class="font-medium">Filter: {label}</span>
			{#if active}
				<Button variant="quiet" size="bar" onclick={onclear}>Clear</Button>
			{/if}
		</div>
		{#if kind === 'values'}
			<div class="grid gap-1.5 px-1.5 pb-1.5">
				{#if options.length > 8}
					<!-- svelte-ignore a11y_autofocus -->
					<input
						class={FIELD}
						placeholder="Search values…"
						aria-label="Search {label.toLowerCase()} values"
						bind:value={query}
						autofocus
					/>
				{/if}
				<div class="max-h-64 overflow-y-auto" role="group" aria-label="{label} values">
					{#each shown as o (o.value)}
						<label
							class="flex h-8 cursor-pointer items-center gap-2 rounded-md px-1.5 hover:bg-muted"
						>
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
				{#each ['min', 'max'] as const as k (k)}
					<label class="grid gap-1 text-xs text-muted-foreground">
						{k === 'min' ? (date ? 'From' : 'At least') : date ? 'To' : 'At most'}
						<input
							class={[FIELD, 'text-foreground']}
							type={date ? 'datetime-local' : 'text'}
							inputmode={date ? undefined : 'decimal'}
							value={range[k] ?? ''}
							onchange={(e) => onrange?.({ ...range, [k]: e.currentTarget.value || undefined })}
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
					value={text}
					autofocus
					onkeydown={(e) => {
						if (e.key === 'Enter') {
							ontext?.(e.currentTarget.value);
							open = false;
						}
					}}
					onchange={(e) => ontext?.(e.currentTarget.value)}
				/>
			</div>
		{/if}
	</Popover.Content>
</Popover.Root>
