<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { goto, invalidate, refreshAll } from '$app/navigation';
	import { page } from '$app/state';
	import type { ResolvedPathname } from '$app/types';
	import { bestRows, chartStyle } from '#lib/chart/grouping.ts';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import { evalTable } from '#lib/eval-table.ts';
	import { evalRuns } from '#lib/evals-view.svelte.ts';
	import LiveStatus from '#lib/components/LiveStatus.svelte';
	import RunView from '#lib/components/RunView.svelte';
	import BestByPicker from '#lib/components/groups/BestByPicker.svelte';
	import DisplayMenu, { type DisplaySetting } from '#lib/components/groups/DisplayMenu.svelte';
	import DeleteGroupDialog from '#lib/components/groups/DeleteGroupDialog.svelte';
	import EditGroupDialog from '#lib/components/groups/EditGroupDialog.svelte';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { clearRunPages } from '#lib/data/cache.ts';
	import type { ProjectEvent } from '#lib/data/live.ts';
	import {
		GROUPS,
		GroupsError,
		classifyColumns,
		displayKey,
		fromFlockFilter,
		groupHref,
		groupsApi,
		groupsCall,
		groupsHref,
		keysOf,
		valueKey,
		viewOf,
		type BestOrder,
		type GroupView,
		type Kind
	} from '#lib/groups.ts';
	import { scheme } from '#lib/theme.svelte.ts';
	import { count } from '#lib/utils.ts';
	import { describeFilters } from '#lib/workspace/filters.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import { Workspace, setWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const entity = $derived(page.params.entity!);
	const project = $derived(page.params.project!);
	/** Outside the org: read-only, nothing saved (D116). */
	const readonly = $derived(data.shell.access === 'public');
	const group = $derived(data.group);

	// ------------------------------------------------------------------------------------------
	// Columns (D218): the API's kinds, or the same rules here when it couldn't say.

	const kinds = $derived.by((): Record<string, Kind> => {
		const out: Record<string, Kind> = data.columns
			? Object.fromEntries(data.columns.map((c) => [c.key, c.kind]))
			: classifyColumns(configs(), group.columns);
		if (group.repeat_key) out[group.repeat_key] = 'repeat';
		return out;
	});
	const varies = $derived(keysOf(kinds, 'varies'));
	/** Each key's number of distinct values, when the API said. */
	const distinct = $derived(
		Object.fromEntries((data.columns ?? []).map((c) => [c.key, c.distinct]))
	);
	const repeatKeys = $derived(keysOf(kinds, 'repeat'));
	const repeatKey = $derived(group.repeat_key ?? repeatKeys[0] ?? null);

	const bestKey = $derived(group.best_key ?? data.groups?.best_key ?? null);
	const bestOrder = $derived<BestOrder | null>(group.best_order ?? data.groups?.best_order ?? null);

	// ------------------------------------------------------------------------------------------
	// The runs, with the Runs page's table and charts (D222): the group's members only.

	/** Its settings as saved, or as just picked while the save is on its way. */
	let viewOverride = $state<GroupView | null>(null);
	const view = $derived<GroupView>(
		viewOverride ?? viewOf(group.view, varies, distinct, keysOf(kinds, 'repeat'))
	);
	/** What each run's configuration is: every varying value but its seed's. */
	const configOf = $derived.by(() => {
		const keys = varies.filter((k) => k !== repeatKey);
		return data.runs.runs.map((_, row) => keys.map((k) => valueKey(ws.cell(row, k))).join('|'));
	});
	/** Whether some of the page's runs repeat a configuration with another seed. */
	const repeats = $derived(repeatKey != null && new Set(configOf).size < configOf.length);
	/** Repeats are drawn as one mean line, unless each run is asked for. */
	const averaged = $derived(repeats && view.seeds !== 'runs');
	/** Only each group's best run (or best configuration's mean) is charted. */
	const showBest = $derived(view.show === 'best' && bestKey != null && bestOrder != null);

	const ws: Workspace = new Workspace(
		() => data.meta,
		() => data.runs,
		() => data.people,
		{
			group: () => data.group.id,
			href: (params) => {
				const qs = params.toString();
				const base = groupHref(entity, project, data.group.id);
				return `${base}${qs ? `?${qs}` : ''}` as ResolvedPathname;
			},
			style: () => {
				const by = view.split;
				const color = view.color;
				const style = chartStyle(
					ws.page.runs.map((r, row) => ({
						name: r.name,
						group: by ? ws.cell(row, by) : undefined,
						color: color ? ws.cell(row, color) : undefined,
						config: averaged ? configOf[row] : null
					})),
					{
						groupBy: by && displayKey(by),
						colorBy: color && displayKey(color),
						band: view.seeds === 'minmax' ? 'minmax' : 'std',
						dark: scheme.dark
					}
				);
				if (!showBest || !bestKey || !bestOrder) return style;
				const only = bestRows(
					ws.page.runs.map((_, row) => ({
						group: by ? ws.cell(row, by) : undefined,
						config: averaged ? configOf[row] : null,
						value: ws.cell(row, bestKey)
					})),
					bestOrder
				);
				return { ...(style ?? { colors: null, means: [], band: 'std', legend: [] }), only };
			}
		}
	);
	setWorkspace(ws);

	/**
	 * A group of eval runs (some run has a suite score; the columns are the project's) opens on
	 * the Evals preset of the table, its runs ranked by suites, as on the Runs page.
	 */
	const evalGroup = $derived(evalTable(evalRuns(ws)).rows.length > 0);

	/** The page's runs' configs, for the kind rules when the API couldn't say. */
	function configs() {
		const keys = data.meta.columns.filter((c) => c.key.startsWith('config.')).map((c) => c.key);
		return data.runs.runs.map((_, row) =>
			Object.fromEntries(keys.map((k) => [k, ws.cell(row, k)]).filter(([, v]) => v != null))
		);
	}

	// The viewer's panel arrangement for this group, once hydrated (the server can't know it).
	$effect(() => {
		void [ws.meta.entity, ws.meta.project, data.group.id];
		untrack(() => ws.restoreLayout());
	});

	// ------------------------------------------------------------------------------------------
	// The table's primary columns: what the assistant reads as this group's (streamed), else its
	// Varies columns; with the best-by column after them.

	let primaryKeys = $state<string[] | null>(null);
	$effect(() => {
		const pending = data.primary;
		primaryKeys = null;
		let current = true;
		void pending.then((keys) => {
			if (current) primaryKeys = keys;
		});
		return () => (current = false);
	});
	const primary = $derived.by(() => {
		const known = new Set(ws.meta.columns.map((c) => c.key));
		const asColumn = (k: string) =>
			known.has(k) ? k : known.has(`config.${k}`) ? `config.${k}` : k;
		const keys = (primaryKeys?.length ? primaryKeys : varies).map(asColumn);
		const all = bestKey && !keys.includes(bestKey) ? [...keys, bestKey] : keys;
		// The viewer's order, as dragged and saved on the group; new suggestions follow it.
		const saved = orderOverride ?? savedOrder(group.view.suggested);
		if (!saved) return all;
		const placed = saved.filter((k) => all.includes(k));
		return [...placed, ...all.filter((k) => !placed.includes(k))];
	});

	/** The suggested columns' order last dragged, while its save is on its way. */
	let orderOverride = $state<string[] | null>(null);
	const savedOrder = (v: unknown) =>
		Array.isArray(v) && v.every((k) => typeof k === 'string') ? (v as string[]) : null;
	function onprimaryorder(keys: string[]) {
		orderOverride = keys;
		void save({ view: { ...group.view, suggested: keys } }, 'the column order').finally(
			() => (orderOverride = null)
		);
	}

	// ------------------------------------------------------------------------------------------
	// Settings saved on the group: its charts' view (D222) and its Best by (D217).

	async function save(body: Record<string, unknown>, what: string) {
		if (readonly) return;
		try {
			await groupsCall(groupsApi(entity, project, String(group.id)), { method: 'PATCH', body });
			await invalidate(GROUPS);
		} catch (err) {
			toast.error(err instanceof GroupsError ? err.message : `Couldn’t save ${what}`);
		}
	}

	function setView(next: GroupView) {
		viewOverride = next;
		void save({ view: { ...group.view, ...next } }, 'the view').finally(
			() => (viewOverride = null)
		);
	}

	const keyOptions = (keys: readonly string[]) => [
		{ value: null, label: 'none' },
		...keys.map((k) => ({ value: k, label: displayKey(k) }))
	];
	const SHOW = [
		{ value: 'all', label: 'All runs' },
		{ value: 'best', label: 'Best per group' }
	];
	const REPEATS = [
		{ value: 'std', label: 'Average (± std)' },
		{ value: 'runs', label: 'Each run' }
	];

	/** The charts' settings, in the Display menu: those that apply to this group's runs. */
	const display = $derived.by((): DisplaySetting[] => {
		const out: DisplaySetting[] = [];
		if (varies.length)
			out.push(
				{
					label: 'Group by',
					value: view.split,
					options: keyOptions(varies),
					onchange: (split) => setView({ ...view, split }),
					title: 'Each value gets a color of its own',
					mono: true
				},
				{
					label: 'Color by',
					value: view.color,
					options: keyOptions([...varies, ...repeatKeys]),
					onchange: (color) => setView({ ...view, color }),
					title: 'Shades each group’s runs light to dark by this key',
					mono: true
				}
			);
		if (bestKey && bestOrder)
			out.push({
				label: 'Show',
				value: showBest ? 'best' : 'all',
				options: SHOW,
				onchange: (show) => setView({ ...view, show: show === 'best' ? 'best' : 'all' }),
				title: `Every run, or only the run with the best ${displayKey(bestKey)} of each ${
					view.split ? displayKey(view.split) : 'group'
				}`
			});
		if (repeats)
			out.push({
				label: 'Repeats',
				value: view.seeds === 'runs' ? 'runs' : 'std',
				options: REPEATS,
				onchange: (seeds) => setView({ ...view, seeds: seeds === 'runs' ? 'runs' : 'std' }),
				title: `Runs with the same config except ${displayKey(
					repeatKey
				)} are drawn as one mean line with a shaded ± std band`
			});
		return out;
	});

	// ------------------------------------------------------------------------------------------
	// The header: the filter in words (D222), in its color's hover and its menu, and editing the
	// group (D223).

	const kindOf = (key: string) => ws.meta.columns.find((c) => c.key === key)?.kind;
	const groupFilters = $derived(fromFlockFilter(group.filter));
	const filterText = $derived(
		groupFilters === null
			? JSON.stringify(group.filter)
			: groupFilters.length
				? describeFilters(groupFilters, kindOf, false, ws.people)
				: null
	);
	/** What the group holds, in words: under its color's hover and atop its menu. */
	const about = $derived(
		`${filterText ? `Runs where ${filterText}` : 'Picked by hand'} · ${count(group.run_count, 'run')}`
	);

	let editing = $state(false);
	/** The "Delete group…" confirmation is open. */
	let deleting = $state(false);
	/** Opened from its new matches, which then come first (D216). */
	let fresh = $state(false);
	function edit(withNew = false) {
		fresh = withNew;
		editing = true;
	}

	// ------------------------------------------------------------------------------------------
	// Live updates (D228): the project renamed, deleted or made private.

	function onproject(e: ProjectEvent) {
		if (e.visibility === 'private') {
			toast(`${project} is no longer public`, { id: 'project' });
			clearRunPages();
			void refreshAll();
		} else if (e.deleted) {
			toast(`${project} was deleted`, { id: 'project' });
			void goto(entityHref(entity), { replaceState: true });
		} else {
			const to = e.renamed_to ?? project;
			toast(`Project renamed to ${to}`, { id: 'project' });
			clearRunPages();
			void goto(groupsHref(entity, to), { replaceState: true });
		}
	}
</script>

<svelte:head>
	<title>{group.name} · {project} · {data.shell.orgName ?? entity}</title>
</svelte:head>

<RunView
	title={group.name}
	initialView={evalGroup ? 'table' : 'visualize'}
	evalPreset
	evalsFirst={evalGroup}
	compactTools
	{primary}
	{onprimaryorder}
	{onproject}
	{readonly}
	mine={false}
	nameW={data.shell.nameW}
	tableW={data.shell.tableW}
	stacked={data.shell.stacked}
	tableH={data.shell.tableH}
>
	{#snippet leading()}
		<!-- A 12px swatch: an icon's weight, not a block beside the 14px title. -->
		<span class="size-3 rounded-[3px]" style:background={group.color} title={about}></span>
	{/snippet}
	{#snippet badges()}
		{#if !readonly}
			<DropdownMenu.Root>
				<DropdownMenu.Trigger>
					{#snippet child({ props })}
						<Button
							{...props}
							variant="ghost"
							size="icon-sm"
							class="relative text-muted-foreground"
							aria-label={group.new_count > 0
								? `Group actions, ${count(group.new_count, 'new match', 'new matches')}`
								: 'Group actions'}
							title="Group actions"
						>
							<EllipsisIcon />
							{#if group.new_count > 0}
								<span
									class="absolute top-0.5 right-0.5 size-1.5 rounded-full bg-state-preempting"
									aria-hidden="true"
								></span>
							{/if}
						</Button>
					{/snippet}
				</DropdownMenu.Trigger>
				<DropdownMenu.Content align="start" class="w-72">
					<DropdownMenu.Label class="font-normal text-muted-foreground">{about}</DropdownMenu.Label>
					<DropdownMenu.Separator />
					<DropdownMenu.Item onSelect={() => edit()}>
						<PencilIcon /> Edit group…
					</DropdownMenu.Item>
					{#if group.new_count > 0}
						<DropdownMenu.Item onSelect={() => edit(true)}>
							<span class="mx-[3px] size-[7px] rounded-full bg-state-preempting"></span>
							Review {count(group.new_count, 'new match', 'new matches')}…
						</DropdownMenu.Item>
					{/if}
					<DropdownMenu.Separator />
					<DropdownMenu.Item variant="destructive" onSelect={() => (deleting = true)}>
						<Trash2Icon /> Delete group…
					</DropdownMenu.Item>
				</DropdownMenu.Content>
			</DropdownMenu.Root>
		{/if}
	{/snippet}
	{#snippet actions()}
		<LiveStatus />
	{/snippet}
	{#snippet trailing()}
		<BestByPicker
			key={bestKey}
			order={bestOrder}
			keys={ws.meta.columns
				.filter((c) => c.key.startsWith('summary.') && c.kind === 'num')
				.map((c) => c.key)}
			disabled={readonly}
			title={group.best_key ? 'This group’s own setting' : 'The project’s setting'}
			onchange={(key, o) => save({ best_key: key, best_order: o }, 'Best by')}
			align="end"
		/>
		<!-- In every view: Group by and Color by color the table's runs as well as the charts. -->
		<DisplayMenu settings={display} />
	{/snippet}
	{#snippet empty()}
		<EmptyState
			variant="onboarding"
			class="empty absolute inset-0 z-4 overflow-y-auto bg-background"
			title="No runs in this group"
		>
			{#snippet lead()}Its runs were deleted or moved away, or none were added yet.{/snippet}
			{#snippet actions()}
				{#if !readonly}
					<Button variant="outline" onclick={() => edit()}>Edit group</Button>
				{/if}
			{/snippet}
		</EmptyState>
	{/snippet}
</RunView>

{#if !readonly}
	<EditGroupDialog bind:open={editing} {group} {fresh} />
	<DeleteGroupDialog bind:open={deleting} {entity} {project} {group} />
{/if}
