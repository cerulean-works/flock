<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import XIcon from '@lucide/svelte/icons/x';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import ColumnFilter, { type FilterValue } from '#lib/components/ColumnFilter.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import BudgetPanel from '#lib/components/home/BudgetPanel.svelte';
	import type { Column } from '#lib/components/DataTable.svelte';
	import LatestRuns from '#lib/components/home/LatestRuns.svelte';
	import PublicOrg from '#lib/components/home/PublicOrg.svelte';
	import ListPage from '#lib/components/ListPage.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
	import WandbKeyCreator from '#lib/components/WandbKeyCreator.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { pollFeed, RUN_STATES, type RunFilters } from '#lib/data/feed.svelte.ts';
	import { wandbSnippet } from '#lib/snippets.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	$effect(() => pollFeed());

	// The columns that filter the list, by column id, and the URL param each sets.
	const FILTERED: Record<string, { key: keyof RunFilters; label: string }> = {
		owner: { key: 'owner', label: 'Owner' },
		project: { key: 'project', label: 'Project' },
		state: { key: 'state', label: 'State' },
		group: { key: 'group', label: 'Group' },
		job: { key: 'job_type', label: 'Job type' },
		gpus: { key: 'gpu', label: 'GPUs' }
	};
	const filtering = $derived(Object.values(data.filters).some((v) => v.length));

	// Nothing logged yet: how to log the first run, instead of an empty table.
	const fresh = $derived(!filtering && !data.feed?.items.length);

	function setParams(changes: Record<string, string | null>) {
		const url = new URL(page.url.href);
		for (const [key, value] of Object.entries(changes)) {
			if (value === null) url.searchParams.delete(key);
			else url.searchParams.set(key, value);
		}
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- this page, other params
		void goto(url, { replace: true, reset: false });
	}
	function setFilter(key: keyof RunFilters, values: string[]) {
		setParams({
			[key]: values.length ? values.join(',') : null,
			// Home's old "my runs" link folds into the Owner filter.
			...(key === 'owner' && { runs: null })
		});
	}

	const person = (id: string) => data.members.find((m) => String(m.id) === id);
	const named = (key: keyof RunFilters, value: string) =>
		key === 'owner'
			? value === 'me'
				? 'Me'
				: (person(value)?.name ?? `Someone (${value})`)
			: key === 'state'
				? (RUN_STATES.find((s) => s.value === value)?.label ?? value)
				: value;

	/** A column's choices, with how many of the org's runs have each. */
	function valuesOf(key: keyof RunFilters): FilterValue[] {
		const facets = data.facets?.[key] ?? [];
		if (key === 'state') {
			const runs = (...states: string[]) =>
				facets.filter((f) => states.includes(String(f.value))).reduce((n, f) => n + f.runs, 0);
			return RUN_STATES.map((st) => ({
				value: st.value,
				label: st.label,
				count: st.value === 'failed' ? runs('failed', 'crashed') : runs('finished'),
				dot: st.value === 'failed' ? 'bg-state-failed' : 'bg-state-finished'
			}));
		}
		const values = facets.map((f) => {
			const value = String(f.value);
			const who = key === 'owner' ? person(value) : undefined;
			return { value, label: named(key, value), count: f.runs, person: who };
		});
		return key === 'owner' ? [{ value: 'me', label: 'Me' }, ...values] : values;
	}

	// The filters in effect, one chip each, under the page's header.
	const chips = $derived(
		Object.values(FILTERED)
			.filter(({ key }) => data.filters[key].length)
			.map(({ key, label }) => ({
				label: `${label}: ${data.filters[key].map((v) => named(key, v)).join(', ')}`,
				clear: () => setFilter(key, [])
			}))
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
	     the arrow (it can't sort by other columns). -->
	{@const f = FILTERED[c.id]}
	<span class="group/h flex w-full items-center gap-1.5">
		<span class={['min-w-0 flex-1 truncate', c.align === 'end' && 'text-right']}>{c.label}</span>
		{#if c.id === 'started'}
			<span class="grid size-4 flex-none place-items-center">
				<ArrowDownIcon class="size-3 text-foreground" aria-label="newest first" />
			</span>
		{/if}
		{#if f}
			<ColumnFilter
				label={f.label}
				active={data.filters[f.key].length > 0}
				values={valuesOf(f.key)}
				selected={data.filters[f.key]}
				onselect={(values) => setFilter(f.key, values)}
				onclear={() => setFilter(f.key, [])}
			/>
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
				empty={filtering ? 'No runs match these filters.' : 'No runs yet.'}
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
