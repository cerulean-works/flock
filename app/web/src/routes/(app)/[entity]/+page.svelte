<script lang="ts">
	import FolderIcon from '@lucide/svelte/icons/folder';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import UsersIcon from '@lucide/svelte/icons/users';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import FeedFilters from '#lib/components/home/FeedFilters.svelte';
	import LatestRuns from '#lib/components/home/LatestRuns.svelte';
	import PublicOrg from '#lib/components/home/PublicOrg.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
	import SegmentedNav from '#lib/components/SegmentedNav.svelte';
	import WandbKeyCreator from '#lib/components/WandbKeyCreator.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { page } from '$app/state';
	import { pollFeed } from '#lib/data/feed.svelte.ts';
	import { wandbSnippet } from '#lib/snippets.ts';
	import { count } from '#lib/utils.ts';
	import { orgHref, withParam } from '#lib/workspace/url.ts';
	import Page from '#lib/components/Page.svelte';
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
{#snippet feedToolbar()}
	<h2 class="text-[13px] font-medium whitespace-nowrap max-sm:sr-only">Latest runs</h2>
	<div class="ml-auto">
		<FeedFilters projects={data.shell.projects.map((p) => p.name)} project={data.project} />
	</div>
	<SegmentedNav
		label="Show"
		items={TABS.map(([key, label]) => ({
			href: withParam(page.url, 'runs', key || null),
			label,
			current: (key === 'mine') === data.mine
		}))}
	/>
{/snippet}

<Page width="full">
	<PageHeader
		title={data.org.display_name}
		toolbar={fresh || data.outside ? undefined : feedToolbar}
		actions={data.outside ? undefined : actions}
	>
		{#snippet subtitle()}
			<span class="inline-flex items-center gap-x-4">
				{#if data.outside}
					<span>Public projects</span>
				{:else}
					<span class="inline-flex items-center gap-1.5">
						<FolderIcon class="size-3.5" aria-hidden="true" />
						{count(data.org.project_count, 'project')}
					</span>
					<span class="inline-flex items-center gap-1.5">
						<UsersIcon class="size-3.5" aria-hidden="true" />
						{count(data.org.member_count, 'member')}
					</span>
				{/if}
				{#if running}
					<span class="inline-flex items-center gap-1.5 text-state-running">
						<span class="size-1.5 rounded-full bg-current" aria-hidden="true"></span>
						{count(running, 'run')} running
					</span>
				{/if}
			</span>
		{/snippet}
	</PageHeader>

	{#if data.outside}
		<main class="relative min-h-0 flex-1 overflow-y-auto pb-10">
			<PublicOrg org={params.entity} projects={data.shell.projects} now={data.now} />
		</main>
	{:else if fresh || !data.feed}
		<main class="relative min-h-0 flex-1 overflow-y-auto">
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
		</main>
	{:else}
		{@const feed = data.feed}
		<main class="relative min-h-0 flex-1 overflow-y-auto pb-10">
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
		</main>
	{/if}
</Page>

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
