<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import BracketsIcon from '@lucide/svelte/icons/brackets';
	import ListFilterIcon from '@lucide/svelte/icons/list-filter';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import XIcon from '@lucide/svelte/icons/x';

	import MenuSearch from './MenuSearch.svelte';
	import { columnLabel } from '#lib/workspace/columns.ts';
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import type { ColumnDef, ColumnKind, RunQuery, RunState } from '#lib/types.ts';
	import {
		countConditions,
		describeFilters,
		isGroup,
		opLabel,
		opsFor,
		parseValue,
		UNARY,
		valueText,
		type FilterMatch,
		type FilterOp,
		type RunFilter,
		type People
	} from '#lib/workspace/filters.ts';
	import { applyRunQuery } from '#lib/workspace/url.ts';
	import { getWorkspace, type Workspace } from '#lib/workspace/workspace.svelte.ts';
	import Autocomplete, { type AutocompleteOption } from './Autocomplete.svelte';
	import FormError from './FormError.svelte';
	import ColumnFilterPanel from './ColumnFilterPanel.svelte';
	import { columnFilterProps } from '#lib/workspace/column-filter.ts';
	import { chipParts, columnFilterKind } from '#lib/workspace/filters.ts';
	import { runValues } from '#lib/data/run-values.svelte.ts';
	import { keyboard } from '#lib/keys.svelte.ts';

	interface Props {
		/**
		 * Filters edited by the caller instead of the workspace's URL (the group pages, 026):
		 * `onapply` gets them, and `columns` and `people` stand in for the workspace's.
		 */
		filters?: RunFilter[];
		columns?: { key: string; kind?: ColumnKind }[];
		people?: People;
		onapply?: (filters: RunFilter[]) => void;
	}

	let { filters, columns: given, people: givenPeople, onapply }: Props = $props();

	// The workspace's, unless the caller edits its own filters (there is no workspace then).
	// svelte-ignore state_referenced_locally
	const ws = onapply ? null : getWorkspace();
	const applied = $derived(filters ?? ws?.page.query.filters ?? []);
	const people = $derived<People>(givenPeople ?? ws?.people ?? new Map());
	const known = $derived<
		{ key: string; kind?: ColumnKind; label?: string; group?: ColumnDef['group'] }[]
	>(given ?? ws?.meta.columns ?? []);
	const count = $derived(countConditions(applied));
	/** Who is looking: their runs' owner reads as Me. */
	const me = $derived(page.data.shell?.me?.id as number | undefined);

	const STATES: RunState[] = ['running', 'preempting', 'finished', 'failed', 'crashed'];

	/** Values offered for columns with few possible ones: users are the org's members. */
	const suggestions = $derived<Partial<Record<ColumnKind, AutocompleteOption[]>>>({
		state: STATES.map((value) => ({ value })),
		bool: [{ value: 'true' }, { value: 'false' }],
		user: [...people.values()].map((p) =>
			p.email ? { value: p.email, hint: p.name } : { value: p.name }
		)
	});

	const KINDS: Record<ColumnKind, string> = {
		num: 'number',
		time: 'date',
		duration: 'duration',
		user: 'person',
		str: 'text',
		state: 'state',
		bool: 'true/false',
		json: 'list'
	};

	const columns = $derived(
		known.map((c) => ({ value: c.key, hint: c.kind ? KINDS[c.kind] : undefined }))
	);

	const kindOf = (key: string) => known.find((c) => c.key === key.trim())?.kind;

	/** A condition as edited: its value still the text typed. */
	interface DraftCondition {
		id: number;
		key: string;
		op: FilterOp;
		text: string;
	}

	interface DraftGroup {
		id: number;
		match: FilterMatch;
		items: DraftItem[];
	}

	type DraftItem = DraftCondition | DraftGroup;

	const isDraftGroup = (d: DraftItem): d is DraftGroup => 'items' in d;

	let lastId = 0;
	const blank = (): DraftCondition => ({ id: ++lastId, key: '', op: 'eq', text: '' });

	function draft(f: RunFilter): DraftItem {
		if (isGroup(f)) return { id: ++lastId, match: f.match, items: f.filters.map(draft) };
		const text = valueText(f.value, kindOf(f.key), people);
		return { id: ++lastId, key: f.key, op: f.op, text };
	}

	let open = $state(false);
	/** Every condition and group edited; its `match` is the toggle in the header. */
	let root = $state<DraftGroup>({ id: 0, match: 'all', items: [] });
	let error = $state<string | null>(null);
	let content = $state<HTMLElement | null>(null);

	/**
	 * Opening edits the filters in effect, or a blank one when there are none. A lone group is
	 * the top level: `any` of several filters travels as one group.
	 */
	function onOpenChange(o: boolean) {
		if (!o) {
			mode = 'pick';
			field = null;
			find = '';
			return;
		}
		error = null;
		const [only] = applied;
		const top =
			applied.length === 1 && isGroup(only) ? only : { match: 'all' as const, filters: applied };
		root = {
			id: 0,
			match: top.match,
			items: top.filters.length ? top.filters.map(draft) : [blank()]
		};
	}

	/** Focuses the column of a new condition, which lists the columns to pick from. */
	async function focusColumn(id: number) {
		await tick();
		content?.querySelector<HTMLInputElement>(`[data-condition="${id}"] input`)?.focus();
	}

	function addCondition(g: DraftGroup) {
		const d = blank();
		g.items.push(d);
		void focusColumn(d.id);
	}

	/** A new group matches what the top level doesn't: `all` of these and `any` of those. */
	function addGroup() {
		const first = blank();
		root.items.push({
			id: ++lastId,
			match: root.match === 'all' ? 'any' : 'all',
			items: [first, blank()]
		});
		void focusColumn(first.id);
	}

	/** Removes an item; a group left empty goes too, and the top level keeps a blank row. */
	function remove(g: DraftGroup, id: number, parent?: DraftGroup) {
		g.items = g.items.filter((d) => d.id !== id);
		if (g.items.length) return;
		if (parent) remove(parent, g.id);
		else g.items.push(blank());
	}

	/** A new column keeps the operator if it still applies. */
	function setKey(d: DraftCondition, key: string) {
		d.key = key;
		const ops = opsFor(kindOf(key));
		if (!ops.some((o) => o.op === d.op)) d.op = ops[0].op;
	}

	function hrefWith(ws: Workspace, filters: RunFilter[]) {
		const params = new URLSearchParams(page.url.search);
		// A different selection starts from its first page.
		applyRunQuery(params, { ...ws.page.query, filters, page: 1 } satisfies RunQuery);
		return ws.href(params);
	}

	function show(filters: RunFilter[]) {
		if (onapply || !ws) {
			onapply?.(filters);
			return;
		}
		ws.selected.clear();
		void goto(hrefWith(ws, filters), { reset: false });
	}

	/** Filters from drafts, leaving out blank rows and empty groups; null after an error. */
	function collect(items: DraftItem[]): RunFilter[] | null {
		const out: RunFilter[] = [];
		for (const d of items) {
			if (isDraftGroup(d)) {
				const filters = collect(d.items);
				if (!filters) return null;
				if (filters.length) out.push({ match: d.match, filters });
				continue;
			}
			const key = d.key.trim();
			if (!key) continue; // a row left blank
			if (UNARY.has(d.op)) {
				out.push({ key, op: d.op, value: null });
				continue;
			}
			const parsed = parseValue(d.text, kindOf(key), people);
			if ('error' in parsed) {
				error = `${key}: ${parsed.error}`;
				return null;
			}
			out.push({ key, op: d.op, value: parsed.value });
		}
		return out;
	}

	function apply(e: SubmitEvent) {
		e.preventDefault();
		const filters = collect(root.items);
		if (!filters) return;
		open = false;
		show(root.match === 'any' && filters.length > 1 ? [{ match: 'any', filters }] : filters);
	}

	/**
	 * Linear's way in (D: audit P2): the Filter button lists the fields by the names the table
	 * shows; picking one asks for its value, and picking or entering that applies at once. The
	 * builder (groups, and/or) stays a step away, as Advanced, and is where a chip opens.
	 */
	let mode = $state<'pick' | 'build'>('pick');
	let field = $state<string | null>(null);
	let find = $state('');
	let findInput = $state<HTMLInputElement | null>(null);

	const fieldName = (c: (typeof known)[number]) =>
		c.label && c.group ? columnLabel({ label: c.label, group: c.group }) : c.key;
	const fields = $derived.by(() => {
		const q = find.trim().toLowerCase();
		return known
			.map((c) => ({ key: c.key, name: fieldName(c), kind: c.kind }))
			.filter((c) => !q || c.name.toLowerCase().includes(q) || c.key.toLowerCase().includes(q));
	});

	function pickField(key: string) {
		field = key;
	}

	/** The listed columns' values with counts: the page's runs, or the project's. */
	const listed = $derived(
		known.filter((c) => columnFilterKind(c.key, c.kind) === 'values').map((c) => c.key)
	);
	const counted = $derived(
		open && page.params.entity && page.params.project
			? runValues(page.params.entity, page.params.project, listed, ws?.group)
			: {}
	);
	const fieldProps = (key: string) =>
		columnFilterProps(
			known.find((c) => c.key === key) ?? { key },
			fieldName(known.find((c) => c.key === key) ?? { key }),
			applied,
			counted,
			people,
			me,
			show
		);

	// What the palette (Cmd+K, F) filters with: every column by the name it has here, each
	// column's filter the same state its header funnel edits. The counts are read for the
	// palette whether or not this menu is open.
	$effect(() => {
		if (!ws) return;
		const w = ws;
		keyboard.filters = {
			columns: () => known.map((c) => ({ key: c.key, label: fieldName(c), kind: c.kind })),
			props: (key) => {
				const c = known.find((k) => k.key === key) ?? { key };
				const values = runValues(w.meta.entity, w.meta.project, listed, w.group);
				return columnFilterProps(c, fieldName(c), applied, values, people, me, show);
			},
			active: () => applied.length > 0,
			applied: () => applied,
			set: show,
			describe: (chip) => {
				const parts = chipParts(chip, {
					kind: kindOf,
					label: (key) => fieldName(known.find((c) => c.key === key) ?? { key }),
					people,
					me
				});
				return [parts.op, parts.value].filter(Boolean).join(' ');
			},
			clear: () => show([]),
			advanced: edit
		};
		return () => {
			keyboard.filters = null;
		};
	});

	/** F, or the palette: the field list, to add a condition. */
	export function pick() {
		mode = 'pick';
		field = null;
		find = '';
		open = true;
		onOpenChange(true);
	}

	/** A chip under the view bar opens the filters as built, to change or regroup them. */
	export function edit() {
		mode = 'build';
		open = true;
		onOpenChange(true);
	}
</script>

<!-- The field list, then the field's value: one step each, applied on the pick. -->
{#snippet picker()}
	{#if field == null}
		<MenuSearch
			bind:ref={findInput}
			bind:value={find}
			placeholder="Search columns…"
			label="Search columns"
			onkeydown={(e) => {
				if (e.key === 'Enter' && fields.length) {
					e.preventDefault();
					void pickField(fields[0].key);
				}
			}}
		/>
		<ul class="max-h-72 overflow-y-auto p-1" aria-label="Fields">
			{#each fields as f (f.key)}
				<li>
					<button
						type="button"
						class="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[13px] outline-none hover:bg-muted focus-visible:bg-muted"
						onclick={() => pickField(f.key)}
					>
						<span class="min-w-0 flex-1 truncate">{f.name}</span>
					</button>
				</li>
			{:else}
				<li class="px-2 py-2 text-[13px] text-muted-foreground">No column matches</li>
			{/each}
		</ul>
		<div class="flex items-center border-t border-divider p-1">
			{#if count}
				<Button
					variant="quiet"
					size="bar"
					onclick={() => {
						open = false;
						show([]);
					}}
				>
					Clear all
				</Button>
			{/if}
			<span class="flex-1"></span>
			<Button
				variant="quiet"
				size="bar"
				{@attach hint('Groups of conditions, joined by and or or')}
				onclick={() => {
					mode = 'build';
					onOpenChange(true);
				}}
			>
				Advanced
			</Button>
		</div>
	{:else}
		{#key field}
			<ColumnFilterPanel
				{...fieldProps(field)}
				onclose={() => (open = false)}
				onback={() => (field = null)}
			/>
		{/key}
	{/if}
{/snippet}

<!-- The word joining a level's items, the top level's or a group's: picked on the second,
     repeated on the rest. -->
{#snippet joiner(g: DraftGroup, i: number, isRoot: boolean)}
	{#if i === 0}
		{isRoot ? 'where' : ''}
	{:else if i === 1}
		<Select.Root type="single" value={g.match} onValueChange={(v) => (g.match = v as FilterMatch)}>
			<Select.Trigger size="sm" class="h-6 gap-1 px-1.5 text-xs" aria-label="Join conditions with">
				{g.match === 'any' ? 'or' : 'and'}
			</Select.Trigger>
			<Select.Content>
				<Select.Item value="all" label="and">and</Select.Item>
				<Select.Item value="any" label="or">or</Select.Item>
			</Select.Content>
		</Select.Root>
	{:else}
		{g.match === 'any' ? 'or' : 'and'}
	{/if}
{/snippet}

{#snippet condition(d: DraftCondition, g: DraftGroup, parent: DraftGroup | undefined)}
	{@const kind = kindOf(d.key)}
	{@const ops = opsFor(kind)}
	{@const unary = UNARY.has(d.op)}
	<div
		class="grid min-w-0 grid-cols-[minmax(0,1.3fr)_7.5rem_minmax(0,1fr)_auto] gap-1.5"
		data-condition={d.id}
	>
		<Autocomplete
			value={d.key}
			options={columns}
			onValueChange={(key) => setKey(d, key)}
			placeholder="e.g., config.lr"
			aria-label="Column"
			class="font-mono text-[12.5px]"
			mono
		/>
		<Select.Root type="single" value={d.op} onValueChange={(v) => (d.op = v as FilterOp)}>
			<Select.Trigger class="w-full" aria-label="Condition">
				{ops.find((o) => o.op === d.op)?.label ?? opLabel(d.op)}
			</Select.Trigger>
			<Select.Content>
				{#each ops as o (o.op)}
					<Select.Item value={o.op} label={o.label}>{o.label}</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
		<Autocomplete
			bind:value={d.text}
			options={(!unary && kind && suggestions[kind]) || []}
			disabled={unary}
			placeholder={unary
				? ''
				: kind === 'time'
					? 'YYYY-MM-DD'
					: kind === 'num' || kind === 'duration'
						? 'Number'
						: kind === 'user'
							? 'Email'
							: 'Value'}
			aria-label="Value"
		/>
		<Button
			variant="ghost"
			size="icon"
			aria-label="Remove condition"
			{@attach hint('Remove')}
			onclick={() => remove(g, d.id, parent)}
		>
			<XIcon />
		</Button>
	</div>
{/snippet}

<!-- A group's conditions and groups, each after the word joining it to the one before. -->
{#snippet items(g: DraftGroup, parent: DraftGroup | undefined)}
	<div class="grid gap-2">
		{#each g.items as d, i (d.id)}
			<div class="grid grid-cols-[3.75rem_minmax(0,1fr)] items-start gap-2">
				<!-- Centred on the item's first row: a condition's inputs, or a group's first
				     condition (inside its border and padding). -->
				<span
					class={[
						'flex h-8 items-center justify-end text-xs text-muted-foreground',
						isDraftGroup(d) && 'mt-[9px]'
					]}
				>
					{@render joiner(g, i, !parent)}
				</span>
				{#if isDraftGroup(d)}
					<div class="grid gap-2 rounded-lg border border-dashed bg-muted/40 p-2">
						{@render items(d, g)}
						<div class="flex items-center justify-between">
							<Button variant="ghost" size="xs" onclick={() => addCondition(d)}>
								<PlusIcon /> Add condition
							</Button>
							<Button
								variant="ghost"
								size="xs"
								class="text-muted-foreground"
								onclick={() => remove(g, d.id, parent)}
							>
								Remove group
							</Button>
						</div>
					</div>
				{:else}
					{@render condition(d, g, parent)}
				{/if}
			</div>
		{/each}
	</div>
{/snippet}

<div class="flex items-center">
	<Popover.Root bind:open {onOpenChange}>
		<Popover.Trigger>
			{#snippet child({ props })}
				<Button
					{...props}
					variant={count ? 'quiet-on' : 'quiet'}
					size="icon-bar"
					class="relative"
					aria-label={count ? `Filter runs, ${count} applied` : 'Filter runs'}
					{@attach hint(count ? describeFilters(applied, kindOf, true, people) : 'Filter  F')}
				>
					<ListFilterIcon />
					{#if count}
						<span
							class="absolute -top-1 -right-1 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-primary px-1 text-[10px] leading-none font-medium text-primary-foreground tabular-nums"
							aria-hidden="true"
						>
							{count}
						</span>
					{/if}
				</Button>
			{/snippet}
		</Popover.Trigger>
		<Popover.Content
			bind:ref={content}
			align="end"
			collisionPadding={16}
			class={mode === 'pick' ? 'w-72 gap-0 p-0 text-[13px]' : 'w-[min(38rem,calc(100vw-2rem))] p-3'}
			onOpenAutoFocus={(e) => {
				e.preventDefault();
				if (mode === 'pick') {
					findInput?.focus();
					return;
				}
				// Only a blank filter starts in its column, listing the columns; otherwise the
				// list would cover the filters being edited.
				const [first] = root.items;
				if (root.items.length === 1 && !isDraftGroup(first) && !first.key) {
					void focusColumn(first.id);
				} else content?.focus();
			}}
		>
			{#if mode === 'pick'}
				{@render picker()}
			{:else}
				<form class="grid gap-3" onsubmit={apply} novalidate>
					{@render items(root, undefined)}
					<FormError {error} />
					<div class="flex items-center gap-1">
						<Button variant="quiet" size="bar" onclick={() => addCondition(root)}>
							<PlusIcon /> Add condition
						</Button>
						<Button
							variant="quiet"
							size="bar"
							{@attach hint('Conditions that hold together, or any one of them')}
							onclick={addGroup}
						>
							<BracketsIcon /> Add group
						</Button>
						<span class="flex-1"></span>
						{#if count}
							<Button
								variant="quiet"
								size="bar"
								onclick={() => {
									open = false;
									show([]);
								}}
							>
								Clear all
							</Button>
						{/if}
						<Button type="submit" size="sm">Apply</Button>
					</div>
				</form>
			{/if}
		</Popover.Content>
	</Popover.Root>
</div>
