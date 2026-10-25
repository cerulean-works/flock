<script lang="ts">
	import LockIcon from '@lucide/svelte/icons/lock';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SearchIcon from '@lucide/svelte/icons/search';
	import XIcon from '@lucide/svelte/icons/x';
	import DatasetDialog from '#lib/components/datasets/DatasetDialog.svelte';
	import HubOff from '#lib/components/datasets/HubOff.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { bytes, count, dateTime, formatNumber, relativeTime } from '#lib/utils.ts';
	import { datasetHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	const orgName = $derived(
		data.shell.org === params.entity ? (data.shell.orgName ?? params.entity) : params.entity
	);

	let query = $state('');
	const shown = $derived.by(() => {
		const q = query.trim().toLowerCase();
		if (!q) return data.datasets;
		return data.datasets.filter(
			(d) => d.name.toLowerCase().includes(q) || d.description.toLowerCase().includes(q)
		);
	});

	let creating = $state(false);

	// Name, visibility, latest version, rows, size, updated; narrow windows keep name and updated.
	const GRID =
		'grid grid-cols-[minmax(0,1fr)_88px] md:grid-cols-[minmax(0,1fr)_80px_72px_96px_88px] lg:grid-cols-[minmax(0,1fr)_96px_80px_96px_88px_96px] items-center gap-x-4 px-5';
	const MD = 'hidden md:block';
	const LG = 'hidden lg:block';
</script>

<svelte:head>
	<title>Datasets · {orgName}</title>
</svelte:head>

{#snippet search()}
	<label
		class="flex h-8 w-64 min-w-0 items-center gap-2 rounded-lg border border-input bg-background px-2.5 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
	>
		<SearchIcon class="size-4 flex-none text-muted-foreground" />
		<input
			bind:value={query}
			type="search"
			class="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint [&::-webkit-search-cancel-button]:hidden"
			placeholder="Find a dataset"
			aria-label="Find a dataset by name or description"
			autocomplete="off"
			spellcheck="false"
		/>
		{#if query}
			<button
				type="button"
				class="grid size-5 flex-none place-items-center rounded text-muted-foreground hover:text-foreground"
				aria-label="Clear search"
				onclick={() => (query = '')}
			>
				<XIcon class="size-3.5" />
			</button>
		{/if}
	</label>
{/snippet}

<PageHeader title="Datasets" toolbar={data.hub && data.datasets.length ? search : undefined}>
	{#snippet subtitle()}
		{orgName}{data.hub ? ` · ${count(data.datasets.length, 'dataset')}` : ''}
	{/snippet}
	{#snippet actions()}
		{#if data.hub && data.member}
			<Button variant="outline" onclick={() => (creating = true)}>
				<PlusIcon /> <span class="max-sm:sr-only">New dataset</span>
			</Button>
		{/if}
	{/snippet}
</PageHeader>

<main class="min-h-0 flex-1 overflow-y-auto pb-10">
	{#if !data.hub}
		<HubOff />
	{:else if !data.datasets.length}
		<EmptyState variant="onboarding" title="No datasets yet">
			{#snippet lead()}
				Datasets are versioned Lance tables. Every write makes a new version, and older ones stay
				readable.
			{/snippet}
			{#if data.member}
				<p class="mt-6 text-muted-foreground">
					<Button variant="link" class="h-auto p-0 text-base" onclick={() => (creating = true)}>
						Create a dataset
					</Button>, then write its first version with
					<code class="font-mono text-[13px]">flock.hub</code>.
				</p>
			{/if}
		</EmptyState>
	{:else}
		<div
			class={[
				GRID,
				'sticky top-0 z-1 h-10 border-b bg-background text-xs font-medium text-muted-foreground'
			]}
		>
			<span>Name</span>
			<span class={LG}>Visibility</span>
			<span class={[MD, 'text-right']}>Version</span>
			<span class={[MD, 'text-right']}>Rows</span>
			<span class={[MD, 'text-right']}>Size</span>
			<span class="text-right">Updated</span>
		</div>
		<ul>
			{#each shown as d (d.id)}
				<li class={[GRID, 'relative min-h-14 border-b border-divider py-2.5 hover:bg-muted']}>
					<div class="min-w-0">
						<div class="flex items-center gap-2">
							<!-- The link covers the row. -->
							<a
								class="truncate font-medium after:absolute after:inset-0 hover:text-link"
								href={datasetHref(d.org, d.name)}
							>
								{d.name}
							</a>
							{#if d.visibility === 'private'}
								<LockIcon
									class="size-3.5 flex-none text-muted-foreground lg:hidden"
									aria-label="Private"
								/>
							{/if}
						</div>
						{#if d.description || d.mirror}
							<p class="mt-0.5 truncate text-[13px] text-muted-foreground">
								{#if d.mirror}
									Mirror of {d.mirror.repo}{#if d.mirror.config}
										({d.mirror.config}){/if}{#if d.description}
										·
									{/if}
								{/if}{d.description}
							</p>
						{/if}
					</div>
					<span class={LG}>
						<Badge variant="secondary" class="text-muted-foreground">
							{d.visibility === 'private' ? 'Private' : 'Internal'}
						</Badge>
					</span>
					<span class={[MD, 'text-right tabular-nums']}>
						{#if d.latest_version != null}v{d.latest_version}{:else}<span class="text-faint">–</span
							>{/if}
					</span>
					<span class={[MD, 'text-right tabular-nums']}>
						{#if d.rows != null}{formatNumber(d.rows)}{:else}<span class="text-faint">–</span>{/if}
					</span>
					<span class={[MD, 'text-right tabular-nums']}>
						{#if d.bytes != null}{bytes(d.bytes)}{:else}<span class="text-faint">–</span>{/if}
					</span>
					<span class="text-right text-muted-foreground" title={dateTime(d.updated_at)}>
						{relativeTime(d.updated_at, data.now)}
					</span>
				</li>
			{:else}
				<li class="px-5 py-6 text-muted-foreground">No datasets match “{query.trim()}”.</li>
			{/each}
		</ul>
	{/if}
</main>

{#if data.member}<DatasetDialog bind:open={creating} entity={params.entity} {orgName} />{/if}
