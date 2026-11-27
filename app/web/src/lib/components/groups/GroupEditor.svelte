<script lang="ts">
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { onMount, type Snippet } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { goto, invalidate } from '$app/navigation';
	import FilterChip from '#lib/components/FilterChip.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import RunFilters from '#lib/components/RunFilters.svelte';
	import { hint } from '#lib/hint.svelte.ts';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import BarSearch from '#lib/components/BarSearch.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import {
		GROUPS,
		GroupsError,
		MAX_RUNS,
		changes,
		classifyColumns,
		columnKind,
		configOf,
		displayKey,
		draftCounts,
		fromFlockFilter,
		groupHref,
		groupName,
		groupsApi,
		groupsCall,
		groupsHref,
		inDraft,
		isNewMatch,
		keysOf,
		lookOf,
		metricLabel,
		rowsOf,
		runsQueryApi,
		setConditions,
		tick,
		toFlockFilter,
		topConditions,
		wandbGroupOf,
		type AssistAction,
		type BestOrder,
		type Draft,
		type FlockFilter,
		type Group,
		type GroupDetail,
		type GroupList,
		type Look,
		type Reading,
		type RunRow,
		type RunsResult
	} from '#lib/groups.ts';
	import type { Owner } from '#lib/types.ts';
	import { count, formatNumber, formatValue } from '#lib/utils.ts';
	import { chipParts, isGroup, type RunFilter } from '#lib/workspace/filters.ts';
	import { runHref } from '#lib/workspace/url.ts';
	import AssistantPanel, { type Entry } from './AssistantPanel.svelte';
	import HeaderMenu from '#lib/components/HeaderMenu.svelte';
	import { columnLabel } from '#lib/workspace/columns.ts';
	import BestMark from './BestMark.svelte';
	import DeleteGroupDialog from './DeleteGroupDialog.svelte';
	import { INFO_COLUMNS, suggestPrompts } from './editor.ts';

	/**
	 * The create and edit pages (D223): every run of the project, ticked in or out of
	 * the group.
	 * Filters tick the runs they match (on a new group) or offer them as new matches (on a saved
	 * one, D216); hand ticks win over both. The assistant (D224) sets the same filters and ticks,
	 * each step undoable.
	 */

	interface Props {
		entity: string;
		project: string;
		runs: RunsResult;
		summaryKeys: string[];
		people: Owner[];
		groupList: GroupList | null;
		/** The group being edited; none when creating one. */
		group?: GroupDetail | null;
		/** What a new group starts with (`newGroupHref`). */
		prefill?: { runs: string[]; filters: RunFilter[]; name: string | null };
		/** Opened from **N new matches**: they come first. */
		fresh?: boolean;
		/** At the view bar's start: the ways to make a group (Auto, Manual) on the create page. */
		tabs?: Snippet;
	}

	let {
		entity,
		project,
		runs,
		summaryKeys,
		people,
		groupList,
		group = null,
		prefill = { runs: [], filters: [], name: null },
		fresh = false,
		tabs
	}: Props = $props();

	// The draft starts from the props once; later loads (a sidebar change) don't reset it.
	// svelte-ignore state_referenced_locally
	const editing = !!group;
	// svelte-ignore state_referenced_locally
	const initialFilters = group ? fromFlockFilter(group.filter) : prefill.filters;

	const rows = $derived(rowsOf(runs));
	const byId = $derived(new Map(rows.map((r) => [r.id, r])));
	const peopleMap = $derived(new Map(people.map((p) => [p.id, p])));

	// ------------------------------------------------------------------------------------------
	// The draft

	let filters = $state.raw<RunFilter[]>(initialFilters ?? []);
	/** A saved filter the popover can't show: kept as it is until the filters are set anew. */
	// svelte-ignore state_referenced_locally
	let rawFilter = $state.raw<FlockFilter | null>(
		initialFilters === null && group ? group.filter : null
	);
	let matched = $state.raw<Set<string>>(new Set());
	// svelte-ignore state_referenced_locally
	let added = $state.raw<Set<string>>(new Set(editing ? [] : prefill.runs));
	let removed = $state.raw<Set<string>>(new Set());
	// svelte-ignore state_referenced_locally
	const members: ReadonlySet<string> = new Set(group?.runs ?? []);
	// svelte-ignore state_referenced_locally
	const dropped: ReadonlySet<string> = new Set(group?.dropped ?? []);
	// svelte-ignore state_referenced_locally
	const byHand: ReadonlySet<string> = new Set(
		Object.entries(group?.added_by ?? {})
			.filter(([, how]) => how === 'hand')
			.map(([id]) => id)
	);

	const draft = $derived<Draft>({ matched, members, dropped, added, removed, byHand, editing });
	const ids = $derived(rows.map((r) => r.id));
	const counts = $derived(draftCounts(draft, ids));
	const inGroup = $derived(rows.filter((r) => inDraft(draft, r.id)));
	/** Members outside the runs read (the project has more than `MAX_RUNS`): kept as they are. */
	const unseen = $derived([...members].filter((id) => !byId.has(id) && !removed.has(id)).length);
	const total = $derived(counts.in + unseen);
	const kept = $derived([...members].filter((id) => !removed.has(id)).length);
	const toAdd = $derived([...added].filter((id) => !members.has(id)).length);
	const newMatches = $derived(ids.filter((id) => isNewMatch(draft, id) && !added.has(id)));

	// Best by: the draft's own setting, else the project's (D217).
	// svelte-ignore state_referenced_locally
	let best = $state<{ key: string; order: BestOrder } | null>(
		group?.best_key ? { key: group.best_key, order: group.best_order ?? 'asc' } : null
	);
	const bestKey = $derived(best?.key ?? groupList?.best_key ?? null);

	// ------------------------------------------------------------------------------------------
	// Matching: the runs the filters match, read from the API (D77's compiler), never guessed.

	let asked = 0;
	async function match(filter: FlockFilter) {
		const n = ++asked;
		if (!Object.keys(filter).length) {
			matched = new Set();
			return 0;
		}
		try {
			const res = await groupsCall<RunsResult>(runsQueryApi(entity, project), {
				method: 'POST',
				body: { filter, columns: [], all_ids: true, limit: 0 }
			});
			if (n === asked) matched = new Set(res.all_ids ?? res.ids);
			return res.total;
		} catch (err) {
			toast.error(err instanceof GroupsError ? err.message : 'Couldn’t match the filters');
			return null;
		}
	}

	const flockFilterNow = () => rawFilter ?? toFlockFilter(filters);

	onMount(() => void match(flockFilterNow()));

	/** New filters, from the popover, a chip or the assistant; the number of runs they match. */
	function setFilters(next: RunFilter[]) {
		filters = next;
		rawFilter = null;
		return match(toFlockFilter(next));
	}

	// ------------------------------------------------------------------------------------------
	// Ticks

	function setTick(id: string, on: boolean, log = true) {
		const next = tick(draft, id, on);
		added = next.added;
		removed = next.removed;
		if (log && assistant) {
			const name = byId.get(id)?.name ?? id;
			entries.push({ kind: 'manual', text: on ? `You added ${name}` : `You left out ${name}` });
		}
	}

	function tickShown(on: boolean) {
		let d = draft;
		for (const r of shown) {
			const next = tick(d, r.id, on);
			d = { ...d, ...next };
		}
		added = new Set(d.added);
		removed = new Set(d.removed);
	}

	function addAll() {
		added = new Set([...added, ...newMatches]);
	}

	// ------------------------------------------------------------------------------------------
	// What's shown: every run or the group's, searched. Editing, the group's saved runs and its
	// new matches come before the rest (new matches first when that's why the page was opened),
	// by what was saved, so a row doesn't move when it's ticked.

	let view = $state<'all' | 'in'>('all');
	let search = $state('');
	const PAGE = 300;
	let limit = $state(PAGE);

	const shown = $derived.by(() => {
		const q = search.trim().toLowerCase();
		const out = rows.filter(
			(r) =>
				(view === 'all' || inDraft(draft, r.id)) &&
				(!q || r.name.toLowerCase().includes(q) || r.id.toLowerCase().includes(q))
		);
		if (!draft.editing) return out;
		const rank = (id: string) =>
			isNewMatch(draft, id) ? Number(!fresh) : draft.members.has(id) ? Number(fresh) : 2;
		return out.sort((a, b) => rank(a.id) - rank(b.id));
	});
	const allShownIn = $derived(shown.length > 0 && shown.every((r) => inDraft(draft, r.id)));
	const someShownIn = $derived(shown.some((r) => inDraft(draft, r.id)));

	// Columns: what varies among the group's runs (D218), else among every run.
	const varies = $derived.by(() => {
		const source = inGroup.length >= 2 ? inGroup : rows;
		return keysOf(classifyColumns(source.map(configOf)), 'varies').slice(0, 8);
	});

	// The best-by column's values are read on their own (`loadEditor`).
	let metric = $state.raw<{ key: string; values: Map<string, unknown> } | null>(null);
	$effect(() => {
		const key = bestKey;
		if (!key || metric?.key === key) return;
		groupsCall<RunsResult>(runsQueryApi(entity, project), {
			method: 'POST',
			body: { columns: [key], sort: ['-created_at'], limit: MAX_RUNS }
		})
			.then((res) => {
				const values = res.data[key] ?? [];
				metric = { key, values: new Map(res.ids.map((id, i) => [id, values[i]])) };
			})
			.catch(() => {});
	});
	const metricOf = (id: string) =>
		metric && metric.key === bestKey ? metric.values.get(id) : undefined;

	// ------------------------------------------------------------------------------------------
	// The name: a template from the draft (D225), until it's typed over.

	// svelte-ignore state_referenced_locally
	let typed = $state<string | null>(group?.name ?? prefill.name);
	// No template for an empty draft ("0 runs"): the field shows its placeholder.
	const suggested = $derived(
		inGroup.length ? groupName(inGroup.map(configOf), filters, wandbGroupOf(filters)) : ''
	);
	const name = $derived(typed ?? suggested);

	// ------------------------------------------------------------------------------------------
	// The assistant (D224): only when the API says the viewer may use it.

	const assistant = $derived(!!groupList?.assistant);
	const suggestions = $derived(
		suggestPrompts(
			rows,
			rows.filter((r) => !inDraft(draft, r.id))
		)
	);
	const entries = $state<Entry[]>([]);
	/** How to take each action back, by its index in `entries`. */
	// eslint-disable-next-line svelte/prefer-svelte-reactivity -- bookkeeping, never rendered
	const undos = new Map<number, () => void>();
	let asking = $state(false);

	async function ask(request: string) {
		entries.push({ kind: 'request', text: request });
		asking = true;
		try {
			const action = await groupsCall<AssistAction>(groupsApi(entity, project, 'assist'), {
				method: 'POST',
				body: { request, filter: topConditions(filters) }
			});
			const index = entries.length;
			entries.push({ kind: 'action', action, result: '', undone: false });
			undos.set(index, await apply(action, index));
		} catch (err) {
			entries.push({
				kind: 'error',
				text:
					err instanceof GroupsError && err.status === 404
						? 'The assistant is off for this organization.'
						: err instanceof GroupsError
							? err.message
							: 'The assistant didn’t answer. Try again.'
			});
		} finally {
			asking = false;
		}
	}

	function setResult(index: number, result: string) {
		const e = entries[index];
		if (e?.kind === 'action') e.result = result;
	}

	/** Applies an action; returns what takes it back. */
	async function apply(action: AssistAction, index: number): Promise<() => void> {
		switch (action.type) {
			case 'filter': {
				const before = filters;
				const beforeRaw = rawFilter;
				const n = await setFilters(setConditions(filters, action.conditions));
				if (n != null) setResult(index, `${count(n, 'run')} match`);
				return () => {
					filters = before;
					rawFilter = beforeRaw;
					void match(flockFilterNow());
				};
			}
			case 'add_runs':
			case 'drop_runs': {
				const on = action.type === 'add_runs';
				const before = action.runs.map((r) => ({
					id: r.id,
					added: added.has(r.id),
					removed: removed.has(r.id)
				}));
				for (const r of action.runs) setTick(r.id, on, false);
				return () => {
					// Copies, filled here and then assigned whole: never mutated once they're state.
					// eslint-disable-next-line svelte/prefer-svelte-reactivity -- rebuilt, then assigned
					const a = new Set(added);
					// eslint-disable-next-line svelte/prefer-svelte-reactivity -- rebuilt, then assigned
					const d = new Set(removed);
					for (const b of before) {
						if (b.added) a.add(b.id);
						else a.delete(b.id);
						if (b.removed) d.add(b.id);
						else d.delete(b.id);
					}
					added = a;
					removed = d;
				};
			}
			case 'set_best': {
				const before = best;
				if (action.best) best = { key: action.best.key, order: action.best.order };
				return () => (best = before);
			}
			default:
				return () => {};
		}
	}

	function undo(index: number) {
		const e = entries[index];
		if (e?.kind !== 'action' || e.undone) return;
		undos.get(index)?.();
		e.undone = true;
	}

	/** Another reading of a condition the assistant set, in its place. */
	async function pick(index: number, k: number, reading: Reading) {
		const e = entries[index];
		if (e?.kind !== 'action') return;
		const old = e.action.conditions[k];
		const rest = filters.filter((f) => isGroup(f) || f.key !== old.key);
		const n = await setFilters(setConditions(rest, [reading]));
		const others = [
			{ key: old.key, op: old.op, value: old.value, probability: old.probability },
			...old.alternatives.filter((a) => a !== reading)
		].sort((a, b) => b.probability - a.probability);
		e.action.conditions[k] = { ...reading, alternatives: others };
		if (n != null) setResult(index, `${count(n, 'run')} match`);
	}

	// ------------------------------------------------------------------------------------------
	// Saving

	let saving = $state(false);
	let confirmDelete = $state(false);

	async function save() {
		const trimmed = name.trim();
		if (!trimmed) {
			toast.error('Name the group');
			return;
		}
		saving = true;
		const filter = flockFilterNow();
		try {
			if (!group) {
				const created = await groupsCall<Group>(groupsApi(entity, project), {
					method: 'POST',
					// Exactly the ticked runs: matches left out are recorded as dropped (D216).
					body: {
						name: trimmed,
						filter,
						runs: inGroup.map((r) => r.id),
						...(best ? { best_key: best.key, best_order: best.order } : {})
					}
				});
				await invalidate(GROUPS);
				toast.success(`Created ${created.name}`);
				await goto(groupHref(entity, project, created.id));
			} else {
				const { add, drop } = changes(draft);
				await groupsCall<GroupDetail>(groupsApi(entity, project, String(group.id)), {
					method: 'PATCH',
					body: {
						...(trimmed !== group.name ? { name: trimmed } : {}),
						filter,
						add,
						drop,
						loaded: true,
						...(best && (best.key !== group.best_key || best.order !== group.best_order)
							? { best_key: best.key, best_order: best.order }
							: {})
					}
				});
				await invalidate(GROUPS);
				toast.success(`Saved ${trimmed}`);
				await goto(groupHref(entity, project, group.id));
			}
		} catch (err) {
			toast.error(err instanceof GroupsError ? err.message : 'Couldn’t save the group');
		} finally {
			saving = false;
		}
	}

	// ------------------------------------------------------------------------------------------
	// Display

	const filterColumns = $derived(
		[...INFO_COLUMNS, 'name', ...runs.columns, ...summaryKeys]
			.filter((k, i, all) => all.indexOf(k) === i)
			.map((key) => ({ key, kind: columnKind(key, runs.data[key] ?? []) }))
	);
	const kindOf = (key: string) => filterColumns.find((c) => c.key === key)?.kind;
	/** The draft's counts, those that aren't zero, with their rows' tint. */
	const tallies = $derived(
		(editing
			? [
					{ n: total, label: count(total, 'run'), swatch: '' },
					{ n: kept, label: `${kept} already in`, swatch: 'bg-state-running/40' },
					{ n: toAdd, label: `${toAdd} to add`, swatch: 'bg-state-finished/50' },
					{ n: counts.out, label: `${counts.out} to drop`, swatch: 'bg-muted-foreground/30' },
					{
						n: newMatches.length,
						label: count(newMatches.length, 'new match', 'new matches'),
						swatch: 'bg-state-preempting/60'
					},
					{ n: counts.dropped, label: `${counts.dropped} dropped`, swatch: '' },
					{ n: unseen, label: `${count(unseen, 'older member')} not listed`, swatch: '' }
				]
			: [
					{
						n: counts.filter,
						label: `${counts.filter} match filters`,
						swatch: 'bg-state-running/40'
					},
					{ n: counts.hand, label: `${counts.hand} by hand`, swatch: 'bg-state-finished/50' },
					{ n: counts.out, label: `${counts.out} left out`, swatch: 'bg-muted-foreground/30' }
				]
		).filter((t) => t.n > 0)
	);

	const LOOK_ROW: Record<Look, string> = {
		filter: 'bg-state-running/6',
		hand: 'bg-state-finished/8',
		out: '',
		member: 'bg-state-running/6',
		new: 'bg-state-preempting/8',
		dropped: '',
		none: ''
	};

	const NAME_W = 260;
	const COL_W = 104;
	const columns = $derived([
		// Labelled as the Runs page labels them.
		{ key: 'state', label: columnLabel({ label: 'state', group: 'info' }), w: 112, num: false },
		// W&B's group only when some run has one: a column of dashes says nothing.
		...(rows.some((r) => r.group != null && r.group !== '')
			? [
					{
						key: 'group',
						label: columnLabel({ label: 'group', group: 'info' }),
						w: 132,
						num: false
					}
				]
			: []),
		// Numbers right-aligned under their header, as on the group's own page.
		...varies.map((k) => ({
			key: k,
			label: displayKey(k),
			w: COL_W,
			num:
				rows.some((r) => typeof r.cells[k] === 'number') &&
				rows.every((r) => r.cells[k] == null || typeof r.cells[k] === 'number')
		})),
		// Best by (D217): its column, or an empty one to pick it from while it's unset.
		...(bestKey || summaryKeys.length
			? [{ key: '$best', label: bestKey ? metricLabel(bestKey) : 'Best by', w: 128, num: true }]
			: [])
	]);
	// The table fills its pane: each column's width is its least, and they share what's left,
	// so a few columns read as a table rather than a strip cut off mid-row.
	const tableW = $derived(NAME_W + columns.reduce((a, c) => a + c.w, 0));

	const cellText = (r: RunRow, key: string) => {
		const v = key === '$best' ? metricOf(r.id) : r.cells[key];
		return typeof v === 'number' ? formatNumber(v) : formatValue(v);
	};

	const NAME_CELL =
		'sticky left-0 z-2 flex flex-none items-center gap-2.5 pr-3 pl-4 shadow-[inset_-1px_0_0_var(--divider)]';
	const CELL = 'flex min-w-0 items-center overflow-hidden px-3 whitespace-nowrap';
	const BUTTON_FOCUS = 'outline-none focus-visible:ring-3 focus-visible:ring-ring/50';
</script>

<div class="flex min-h-0 flex-1 flex-col">
	<PageHeader
		title={name.trim() || 'New group'}
		trail={[{ label: 'Groups', href: groupsHref(entity, project) }]}
		phoneTrail={false}
	>
		{#snippet leading()}
			<span
				class={['size-3 rounded-[3px]', !group && 'border-[1.5px] border-dashed border-faint']}
				style:background={group?.color}
				aria-hidden="true"
			></span>
		{/snippet}
		{#snippet heading()}
			<!-- The name, edited in place: the trail's last step (D225). -->
			<label class="flex min-w-0 max-sm:flex-1">
				<span class="sr-only">Group name</span>
				<input
					class="-mx-1 field-sizing-content h-6 max-w-80 min-w-24 truncate rounded px-1 text-[13px] leading-5 font-medium text-foreground shadow-[inset_0_-1px_0_var(--border)] outline-none placeholder:text-faint hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:shadow-none {BUTTON_FOCUS}"
					aria-label="Group name"
					value={name}
					oninput={(e) => (typed = e.currentTarget.value)}
					placeholder="New group"
					maxlength={200}
				/>
			</label>
		{/snippet}
		{#snippet actions()}
			<!-- What the group would be, by how each run came to be in or out (D223): one muted
			     line, each count's square the tint of its rows. -->
			<p
				class="flex min-w-0 items-center gap-x-3 truncate text-[13px] text-muted-foreground tabular-nums max-md:hidden"
				role="status"
			>
				{#each tallies as t (t.label)}
					<span class="flex flex-none items-center gap-1.5">
						{#if t.swatch}<span class="size-2 rounded-[2px] {t.swatch}"></span>{/if}
						{t.label}
					</span>
				{/each}
				{#if newMatches.length}
					<button
						type="button"
						class="flex-none rounded-sm text-link hover:underline {BUTTON_FOCUS}"
						onclick={addAll}
					>
						Add all
					</button>
				{/if}
			</p>
			{#if group}
				<Button
					variant="quiet"
					size="icon-bar"
					class="hover:text-destructive"
					aria-label="Delete group"
					{@attach hint('Delete group (its runs stay)')}
					onclick={() => (confirmDelete = true)}
				>
					<Trash2Icon />
				</Button>
			{/if}
			<Button
				variant="quiet"
				size="bar"
				href={group ? groupHref(entity, project, group.id) : groupsHref(entity, project)}
			>
				Cancel
			</Button>
			<Button
				size="bar"
				disabled={saving || !name.trim() || (!group && total === 0)}
				onclick={save}
			>
				{group ? 'Save' : `Create · ${count(total, 'run')}`}
			</Button>
		{/snippet}
		{#snippet toolbar()}
			<!-- Tabs at the left and the icons at the right, as on the group's own page. -->
			{#if tabs}
				{@render tabs()}
				<span class="mx-2 h-4 w-px bg-divider" aria-hidden="true"></span>
			{/if}
			<Tabs.Root value={view} onValueChange={(v) => (view = v as 'all' | 'in')}>
				<Tabs.List aria-label="Show">
					<Tabs.Trigger value="all">All runs · {rows.length}</Tabs.Trigger>
					<Tabs.Trigger value="in">In group · {counts.in}</Tabs.Trigger>
				</Tabs.List>
			</Tabs.Root>
			<span class="flex-1"></span>
			<BarSearch bind:value={search} things="runs" />
			<RunFilters
				{filters}
				columns={filterColumns}
				people={peopleMap}
				onapply={(next) => void setFilters(next)}
			/>
		{/snippet}
	</PageHeader>
	{#if rawFilter || filters.length}
		<!-- The group's rule, one chip per condition; the cross drops one. Not a filter on the
		     list below: All runs lists every run, ticked or not. -->
		<div
			class="flex min-h-9 flex-none flex-wrap items-center gap-1.5 border-b border-divider py-1 pr-4 pl-5"
			aria-label="The group’s rule"
			role="group"
		>
			<span
				class="mr-1 text-xs text-muted-foreground"
				{@attach hint('Runs matching this join the group; All runs still lists every run')}
				>Rule</span
			>
			{#if rawFilter}
				<span
					class="h-6 truncate rounded-md bg-muted px-2 py-0.5 font-mono text-[12px] leading-5"
					{@attach hint('This filter can’t be edited here; setting filters replaces it')}
				>
					{JSON.stringify(rawFilter)}
				</span>
			{/if}
			{#each filters as f, i (i)}
				<FilterChip
					parts={chipParts(f, { label: displayKey, kind: kindOf, people: peopleMap })}
					onremove={() => void setFilters(filters.filter((_, j) => j !== i))}
				/>
			{/each}
		</div>
	{/if}

	<div class="flex min-h-0 flex-1">
		<main class="flex min-w-0 flex-1 flex-col">
			<div class="relative min-h-0 flex-1">
				<div class="absolute inset-0 overflow-auto overscroll-contain text-[13px]">
					<div
						class="sticky top-0 z-3 flex h-9 items-stretch border-b bg-background text-xs font-medium text-muted-foreground"
						style:min-width="{tableW}px"
					>
						<div class={[NAME_CELL, 'bg-background']} style:width="{NAME_W}px">
							<Checkbox
								checked={allShownIn}
								indeterminate={someShownIn && !allShownIn}
								onCheckedChange={() => tickShown(!allShownIn)}
								aria-label={allShownIn ? 'Leave out every shown run' : 'Add every shown run'}
							/>
							<span class="text-foreground">Name</span>
							<span class="ml-auto font-normal text-faint">{shown.length}</span>
						</div>
						{#each columns as c (c.key)}
							<div
								class={[CELL, c.num && 'group/h justify-end gap-1']}
								style:flex="1 0 {c.w}px"
								title={c.key.startsWith('$') ? undefined : c.key}
							>
								<span class={['truncate', c.key === '$best' && !bestKey && 'text-faint']}
									>{c.label}</span
								>
								{#if c.key === '$best'}
									{@const order = best?.order ?? groupList?.best_order ?? null}
									{#if bestKey}
										<BestMark
											{order}
											source={best ? 'this group’s own setting' : 'the project’s setting'}
										/>
									{/if}
									<HeaderMenu
										label={c.label}
										best={{
											active: !!bestKey,
											order,
											onbest: (o) => {
												if (bestKey) best = { key: bestKey, order: o };
											},
											keys: summaryKeys.filter((k) => k !== bestKey),
											onpick: (k) => (best = { key: k, order: order ?? 'asc' })
										}}
									/>
								{/if}
							</div>
						{/each}
					</div>
					{#each shown.slice(0, limit) as r (r.id)}
						{@const look = lookOf(draft, r.id)}
						{@const ticked = inDraft(draft, r.id)}
						{@const struck = look === 'out' || look === 'dropped'}
						<div
							class={[
								'group/row flex h-9 items-stretch border-b border-divider tabular-nums',
								LOOK_ROW[look]
							]}
							style:min-width="{tableW}px"
						>
							<div class={[NAME_CELL, 'bg-background']} style:width="{NAME_W}px">
								<!-- The row's wash, over the sticky cell's opaque background. -->
								<span class={['pointer-events-none absolute inset-0', LOOK_ROW[look]]}></span>
								<Checkbox
									class="relative"
									checked={ticked}
									onCheckedChange={(on) => setTick(r.id, on)}
									aria-label="In group: {r.name}"
								/>
								<a
									class={[
										'relative min-w-0 flex-1 truncate underline-offset-3 hover:text-link hover:underline',
										struck ? 'text-faint line-through' : 'text-foreground'
									]}
									href={runHref(entity, project, r.id)}
									target="_blank"
									title={r.name}>{r.name}</a
								>
							</div>
							{#each columns as c (c.key)}
								<div class={[CELL, c.num && 'justify-end']} style:flex="1 0 {c.w}px">
									{#if c.key === 'state'}
										<StateBadge state={r.state} />
									{:else}
										{@const text = c.key === 'group' ? formatValue(r.group) : cellText(r, c.key)}
										<span class={['truncate', text === '–' && 'text-faint']} title={text}
											>{text}</span
										>
									{/if}
								</div>
							{/each}
						</div>
					{/each}
					{#if shown.length > limit}
						<div class="flex h-9 items-center pl-4" style:min-width="{tableW}px">
							<button
								type="button"
								class="sticky left-4 text-xs text-link hover:underline"
								onclick={() => (limit += PAGE)}
							>
								Show {Math.min(PAGE, shown.length - limit)} more of {shown.length - limit}
							</button>
						</div>
					{/if}
					{#if !shown.length}
						<p class="px-4 py-6 text-muted-foreground">
							{view === 'in'
								? 'No run is in the group yet. Set filters, or tick runs in All runs.'
								: 'No run matches the search.'}
						</p>
					{/if}
				</div>
			</div>
		</main>
		{#if assistant}
			<AssistantPanel
				{entries}
				pending={asking}
				onsend={ask}
				onundo={undo}
				onpick={pick}
				{suggestions}
			/>
		{/if}
	</div>
</div>

{#if group}
	<DeleteGroupDialog bind:open={confirmDelete} {entity} {project} {group} />
{/if}
