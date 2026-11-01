<script lang="ts">
	import BracketsIcon from '@lucide/svelte/icons/brackets';
	import ListFilterIcon from '@lucide/svelte/icons/list-filter';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import UserIcon from '@lucide/svelte/icons/user';
	import XIcon from '@lucide/svelte/icons/x';
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import type { ColumnKind, RunQuery, RunState } from '#lib/types.ts';
	import {
		countConditions,
		describeFilters,
		isGroup,
		opLabel,
		opsFor,
		ownedBy,
		parseValue,
		setOwner,
		UNARY,
		valueText,
		type FilterMatch,
		type FilterOp,
		type RunFilter
	} from '#lib/workspace/filters.ts';
	import { applyRunQuery, workspaceHref } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import Autocomplete, { type AutocompleteOption } from './Autocomplete.svelte';
	import FormError from './FormError.svelte';

	const ws = getWorkspace();
	const applied = $derived(ws.page.query.filters);
	const count = $derived(countConditions(applied));
	/** Who is looking: "My runs" keeps the runs they logged. */
	const me = $derived(page.data.shell?.me.id);
	const mine = $derived(me != null && ownedBy(applied, me));

	const STATES: RunState[] = ['running', 'preempting', 'finished', 'failed', 'crashed'];

	/** Values offered for columns with few possible ones: users are the org's members. */
	const suggestions = $derived<Partial<Record<ColumnKind, AutocompleteOption[]>>>({
		state: STATES.map((value) => ({ value })),
		bool: [{ value: 'true' }, { value: 'false' }],
		user: [...ws.people.values()].map((p) => ({ value: p.email, hint: p.name }))
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

	const columns = $derived(ws.meta.columns.map((c) => ({ value: c.key, hint: KINDS[c.kind] })));

	const kindOf = (key: string) => ws.meta.columns.find((c) => c.key === key.trim())?.kind;

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
		const text = valueText(f.value, kindOf(f.key), ws.people);
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
		if (!o) return;
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

	function hrefWith(filters: RunFilter[]) {
		const params = new URLSearchParams(page.url.search);
		// A different selection starts from its first page.
		applyRunQuery(params, { ...ws.page.query, filters, page: 1 } satisfies RunQuery);
		return workspaceHref(ws.meta.entity, ws.meta.project, params);
	}

	function show(filters: RunFilter[]) {
		ws.selected.clear();
		void goto(hrefWith(filters), { reset: false });
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
			const parsed = parseValue(d.text, kindOf(key), ws.people);
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
</script>

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
			placeholder="config.lr"
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
			title="Remove"
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

{#if me != null}
	<Button
		variant={mine ? 'secondary' : 'outline'}
		aria-pressed={mine}
		title={mine ? 'Show everyone’s runs' : 'Show only the runs you logged'}
		onclick={() => show(setOwner(applied, me, !mine))}
	>
		<UserIcon /> My runs
	</Button>
{/if}

<div class="flex items-center">
	<Popover.Root bind:open {onOpenChange}>
		<Popover.Trigger>
			{#snippet child({ props })}
				<Button
					{...props}
					variant={count ? 'secondary' : 'outline'}
					class={count ? 'rounded-r-none' : ''}
					title={count ? describeFilters(applied, kindOf, true, ws.people) : 'Filter runs'}
				>
					<ListFilterIcon /> Filter
					{#if count}
						<span class="tabular-nums">{count}</span>
					{/if}
				</Button>
			{/snippet}
		</Popover.Trigger>
		<Popover.Content
			bind:ref={content}
			align="start"
			class="w-[min(38rem,calc(100vw-2rem))] p-3"
			onOpenAutoFocus={(e) => {
				// Only a blank filter starts in its column, listing the columns; otherwise the
				// list would cover the filters being edited.
				e.preventDefault();
				const [first] = root.items;
				if (root.items.length === 1 && !isDraftGroup(first) && !first.key) {
					void focusColumn(first.id);
				} else content?.focus();
			}}
		>
			<form class="grid gap-3" onsubmit={apply} novalidate>
				{@render items(root, undefined)}
				<FormError {error} />
				<div class="flex items-center gap-1">
					<Button variant="ghost" size="sm" onclick={() => addCondition(root)}>
						<PlusIcon /> Add condition
					</Button>
					<Button
						variant="ghost"
						size="sm"
						title="Conditions that hold together, or any one of them"
						onclick={addGroup}
					>
						<BracketsIcon /> Add group
					</Button>
					<span class="flex-1"></span>
					{#if count}
						<Button
							variant="ghost"
							size="sm"
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
		</Popover.Content>
	</Popover.Root>
	{#if count}
		<Button
			variant="secondary"
			size="icon"
			class="rounded-l-none border-l border-background"
			aria-label="Clear filters"
			title="Clear filters"
			onclick={() => show([])}
		>
			<XIcon />
		</Button>
	{/if}
</div>
