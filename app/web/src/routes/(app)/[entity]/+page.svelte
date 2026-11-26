<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import FeedFilters from '#lib/components/home/FeedFilters.svelte';
	import LatestRuns from '#lib/components/home/LatestRuns.svelte';
	import PublicOrg from '#lib/components/home/PublicOrg.svelte';
	import ListPage from '#lib/components/ListPage.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
	import SegmentedNav from '#lib/components/SegmentedNav.svelte';
	import WandbKeyCreator from '#lib/components/WandbKeyCreator.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { page } from '$app/state';
	import { pollFeed } from '#lib/data/feed.svelte.ts';
	import { wandbSnippet } from '#lib/snippets.ts';
	import { orgHref, withParam } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	$effect(() => pollFeed());

	const running = $derived(data.shell.projects.reduce((n, p) => n + p.active_run_count, 0));
	// Nothing logged yet: how to log the first run, instead of an empty table.
	const fresh = $derived(!data.mine && !data.project && !data.feed?.items.length);

	const TABS = [
		['', 'Everyone'],
		['mine', 'Mine']
	] as const;

	let creating = $state(false);
</script>

<svelte:head>
	<title>Runs · {data.org.display_name}</title>
</svelte:head>

{#snippet actions()}
	<Button variant="ghost" href={orgHref(params.entity)}>
		<SettingsIcon /> <span class="max-sm:sr-only">Settings</span>
	</Button>
	<Button variant="outline" onclick={() => (creating = true)}>
		<PlusIcon /> <span class="max-sm:sr-only">New project</span>
	</Button>
{/snippet}

<!-- The feed's own controls, under the title once there are runs to show. -->
{#snippet feedControls()}
	<FeedFilters projects={data.shell.projects.map((p) => p.name)} project={data.project} />
	<SegmentedNav
		label="Show"
		items={TABS.map(([key, label]) => ({
			href: withParam(page.url, 'runs', key || null),
			label,
			current: (key === 'mine') === data.mine
		}))}
	/>
{/snippet}

<ListPage
	title="Runs"
	live={running}
	heading={fresh || data.outside ? undefined : 'Latest runs'}
	controls={fresh || data.outside ? undefined : feedControls}
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
		{#key JSON.stringify(data.query)}
			<LatestRuns
				first={() => feed}
				query={data.query}
				now={data.now}
				empty={data.project
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
