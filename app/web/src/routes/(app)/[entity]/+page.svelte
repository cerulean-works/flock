<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import FunnelIcon from '@lucide/svelte/icons/funnel';
	import XIcon from '@lucide/svelte/icons/x';
	import { hint } from '#lib/hint.svelte.ts';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import BudgetPanel from '#lib/components/home/BudgetPanel.svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import type { Column } from '#lib/components/DataTable.svelte';
	import PickerList from '#lib/components/PickerList.svelte';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import LatestRuns from '#lib/components/home/LatestRuns.svelte';
	import PublicOrg from '#lib/components/home/PublicOrg.svelte';
	import ListPage from '#lib/components/ListPage.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
	import WandbKeyCreator from '#lib/components/WandbKeyCreator.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { pollFeed, RUN_STATES } from '#lib/data/feed.svelte.ts';
	import { wandbSnippet } from '#lib/snippets.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	$effect(() => pollFeed());

	// Nothing logged yet: how to log the first run, instead of an empty table.
	const fresh = $derived(
		!data.mine && data.owner === null && !data.project && !data.state && !data.feed?.items.length
	);

	// The list's filters live in its column headers (Owner, Project, State): a filter icon on
	// hover, kept while the filter is on. The ones in effect also show as chips over the list.
	function setParams(changes: Record<string, string | null>) {
		const url = new URL(page.url.href);
		for (const [key, value] of Object.entries(changes)) {
			if (value === null) url.searchParams.delete(key);
			else url.searchParams.set(key, value);
		}
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- this page, other params
		void goto(url, { replace: true, reset: false });
	}

	const ownerValue = $derived(data.mine ? 'me' : data.owner !== null ? String(data.owner) : 'all');
	function setOwner(value: string) {
		setParams({
			runs: value === 'me' ? 'mine' : null,
			owner: value === 'me' || value === 'all' ? null : value
		});
	}
	const ownerName = $derived(
		data.mine ? 'Me' : (data.members.find((m) => m.id === data.owner)?.name ?? null)
	);
	const active: Record<string, boolean> = $derived({
		owner: ownerName !== null,
		project: !!data.project,
		state: !!data.state
	});
	const FILTERS = ['owner', 'project', 'state'];
	type FilterChoice = {
		value: string;
		label: string;
		person?: { name: string; avatar_url: string | null };
		/** A state's dot colour, as the State column draws it. */
		dot?: string;
	};
	function filterChoices(column: string): FilterChoice[] {
		if (column === 'owner')
			return [
				{ value: 'all', label: 'Everyone' },
				{ value: 'me', label: 'Me' },
				...data.members.map((m) => ({ value: String(m.id), label: m.name, person: m }))
			];
		if (column === 'project')
			return [
				{ value: 'all', label: 'All projects' },
				...data.shell.projects.map((p) => ({ value: p.name, label: p.name }))
			];
		return [
			{ value: 'all', label: 'All states' },
			...RUN_STATES.map((st) => ({
				value: st.value,
				label: st.label,
				dot: st.value === 'failed' ? 'bg-state-failed' : 'bg-state-finished'
			}))
		];
	}
	const filterValue = (column: string) =>
		column === 'owner'
			? ownerValue
			: column === 'project'
				? (data.project ?? 'all')
				: (data.state ?? 'all');
	function pickFilter(column: string, value: string) {
		if (column === 'owner') setOwner(value);
		else setParams({ [column]: value === 'all' ? null : value });
	}
	let filterQuery = $state('');
	let filterActive = $state(0);

	const chips = $derived(
		[
			ownerName && { label: `Owner: ${ownerName}`, clear: () => setOwner('all') },
			data.project && {
				label: `Project: ${data.project}`,
				clear: () => setParams({ project: null })
			},
			data.state && {
				label: `State: ${RUN_STATES.find((s) => s.value === data.state)?.label ?? data.state}`,
				clear: () => setParams({ state: null })
			}
		].filter((c) => !!c)
	);

	let creating = $state(false);
</script>

<svelte:head>
	<title>Runs · {data.org.display_name}</title>
</svelte:head>

{#snippet actions()}
	<!-- The org's month: one line here, its breakdown on request (on a phone, above the list). -->
	{#if data.budget}
		<BudgetPanel
			budget={data.budget}
			thisMonth={data.thisMonth}
			admin={data.shell.access === 'admin'}
			class="max-sm:hidden"
		/>
	{/if}
{/snippet}

{#snippet head(c: Column)}
	<!-- The slurm dashboard's header: the label, then a funnel that shows on hover and stays,
	     filled, while the column filters the list. The list is newest first: Updated carries
	     the arrow. -->
	<span class={['group/h flex w-full items-center gap-1', c.align === 'end' && 'justify-end']}>
		<span class="truncate">{c.label}</span>
		{#if c.id === 'started'}
			<ArrowDownIcon class="size-3 flex-none text-foreground" aria-label="newest first" />
		{/if}
		{#if FILTERS.includes(c.id)}
			<Popover.Root
				onOpenChange={(open) => {
					if (!open) return;
					filterQuery = '';
					filterActive = 0;
				}}
			>
				<Popover.Trigger>
					{#snippet child({ props })}
						<button
							{...props}
							type="button"
							class={[
								'relative z-2 grid size-5 flex-none place-items-center rounded outline-none hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:ring-3 focus-visible:ring-ring/50 data-[state=open]:bg-muted data-[state=open]:opacity-100',
								active[c.id]
									? 'text-primary'
									: 'opacity-0 group-hover/h:opacity-100 pointer-coarse:opacity-100'
							]}
							aria-label="Filter {c.label.toLowerCase()}"
							{@attach hint(`Filter ${c.label.toLowerCase()}`)}
						>
							<FunnelIcon class={['size-3', active[c.id] && 'fill-current']} />
						</button>
					{/snippet}
				</Popover.Trigger>
				<Popover.Content
					align="start"
					class="w-64 gap-0 p-0 text-[13px] font-normal text-foreground"
				>
					<div class="flex h-9 items-center justify-between border-b border-divider pr-1.5 pl-3">
						<span class="font-medium">Filter: {c.label}</span>
						{#if active[c.id]}
							<Button variant="quiet" size="bar" onclick={() => pickFilter(c.id, 'all')}
								>Clear</Button
							>
						{/if}
					</div>
					{@const choices = filterChoices(c.id)}
					{@const q = filterQuery.trim().toLowerCase()}
					<PickerList
						options={q ? choices.filter((o) => o.label.toLowerCase().includes(q)) : choices}
						key={(o) => o.value}
						current={(o) => o.value === filterValue(c.id)}
						noun={c.id === 'owner' ? 'people' : c.id === 'project' ? 'projects' : 'states'}
						label="Search {c.label.toLowerCase()}"
						placeholder="Search…"
						class="max-h-72"
						bind:query={filterQuery}
						bind:active={filterActive}
						onpick={(o) => pickFilter(c.id, o.value)}
					>
						{#snippet row(o: FilterChoice)}
							{#if o.dot}
								<span class={['size-2 flex-none rounded-full', o.dot]} aria-hidden="true"></span>
							{/if}
							{#if o.person}
								<Avatar name={o.person.name} src={o.person.avatar_url} class="size-4 text-[8px]" />
							{/if}
							<span class="min-w-0 flex-1 truncate">{o.label}</span>
						{/snippet}
					</PickerList>
				</Popover.Content>
			</Popover.Root>
		{/if}
	</span>
{/snippet}

<ListPage
	title={data.outside ? data.org.display_name : 'Runs'}
	actions={data.outside ? undefined : actions}
>
	{#snippet subtitle()}
		{#if data.outside}<span>Public projects</span>{/if}
	{/snippet}

	{#if data.outside}
		<PublicOrg org={params.entity} projects={data.shell.projects} now={data.now} />
	{:else if fresh || !data.feed}
		<EmptyState variant="onboarding" title="Log your first run">
			{#snippet lead()}Runs from every project show up here once they’re logged.{/snippet}
			<ol class="grid gap-7">
				<li class="grid gap-2.5">
					<p class="flex items-baseline gap-2.5">
						<span class="step">1</span>Install the SDK
					</p>
					<CopyBlock lang="bash" code="pip install wandb" />
				</li>
				<li class="grid gap-2.5">
					<p class="flex items-baseline gap-2.5">
						<span class="step">2</span>
						<span>
							Log in to
							<span class="font-mono text-[0.9em]">{data.flockUrl}</span> with this key
						</span>
					</p>
					<WandbKeyCreator />
				</li>
				<li class="grid gap-2.5">
					<p class="flex items-baseline gap-2.5">
						<span class="step">3</span>Log a run to {data.org.display_name}
					</p>
					<CopyBlock lang="python" code={wandbSnippet(params.entity, 'my-project')} />
				</li>
			</ol>
			{#snippet actions()}
				<Button variant="ghost" onclick={() => (creating = true)}>
					<PlusIcon /> Create an empty project
				</Button>
			{/snippet}
		</EmptyState>
	{:else}
		{@const feed = data.feed}
		{#if data.budget}
			<BudgetPanel
				budget={data.budget}
				thisMonth={data.thisMonth}
				admin={data.shell.access === 'admin'}
				class="mx-3 mt-2 sm:hidden"
			/>
		{/if}
		{#if chips.length}
			<!-- The filters in effect, one chip each (the project Runs' look); the cross drops one. -->
			<div
				class="flex min-h-9 flex-wrap items-center gap-1.5 border-b border-divider py-1 pr-4 pl-5"
				aria-label="Filters"
				role="group"
			>
				{#each chips as chip (chip.label)}
					<span class="inline-flex h-6 max-w-80 items-center rounded-md bg-muted text-xs">
						<span class="min-w-0 truncate py-0.5 pr-1 pl-2">{chip.label}</span>
						<button
							type="button"
							class="grid h-6 w-6 flex-none place-items-center rounded-r-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
							aria-label="Remove filter {chip.label}"
							onclick={chip.clear}
						>
							<XIcon class="size-3" />
						</button>
					</span>
				{/each}
			</div>
		{/if}
		{#key JSON.stringify(data.query)}
			<LatestRuns
				first={() => feed}
				query={data.query}
				now={data.now}
				mine={data.mine}
				{head}
				empty={data.state
					? `No ${data.state === 'failed' ? 'failed' : 'finished'} runs${data.project ? ` in ${data.project}` : ''}${data.mine ? ' by you' : ''}.`
					: data.project
						? `No runs in ${data.project}${data.mine ? ' by you' : ''} yet.`
						: data.mine
							? 'You haven’t logged any runs here yet.'
							: 'No runs yet.'}
			/>
		{/key}
	{/if}
</ListPage>

{#if !data.outside}<ProjectDialog bind:open={creating} entity={params.entity} />{/if}

<style>
	/* The steps' numbers: small, quiet and the same width, so the step text lines up. */
	.step {
		display: inline-grid;
		flex: none;
		place-items: center;
		width: 1.25rem;
		height: 1.25rem;
		border-radius: 9999px;
		background: var(--muted);
		color: var(--muted-foreground);
		font-size: 12px;
		font-variant-numeric: tabular-nums;
		transform: translateY(-1px);
	}
</style>
