<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import FeedFilters from '#lib/components/home/FeedFilters.svelte';
	import LatestRuns from '#lib/components/home/LatestRuns.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
	import SegmentedNav from '#lib/components/SegmentedNav.svelte';
	import WandbKeyCreator from '#lib/components/WandbKeyCreator.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { page } from '$app/state';
	import { pollFeed } from '#lib/data/feed.svelte.ts';
	import { count } from '#lib/utils.ts';
	import { orgHref, withParam } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	$effect(() => pollFeed());

	const running = $derived(data.shell.projects.reduce((n, p) => n + p.active_run_count, 0));
	// Nothing logged yet: how to log the first run, instead of an empty table.
	const fresh = $derived(!data.mine && !data.project && !data.feed.items.length);

	const TABS = [
		['', 'Everyone'],
		['mine', 'Mine']
	] as const;

	let creating = $state(false);
</script>

<svelte:head>
	<title>Runs · {data.org.display_name}</title>
</svelte:head>

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

<PageHeader title={data.org.display_name} toolbar={fresh ? undefined : feedToolbar}>
	{#snippet subtitle()}
		{count(data.org.project_count, 'project')} · {count(data.org.member_count, 'member')}
		{#if running}
			· <span class="text-state-running">{count(running, 'run')} running</span>
		{/if}
	{/snippet}
	{#snippet actions()}
		<Button variant="ghost" href={orgHref(params.entity)}>
			<SettingsIcon /> <span class="max-sm:sr-only">Settings</span>
		</Button>
		<Button variant="outline" onclick={() => (creating = true)}>
			<PlusIcon /> <span class="max-sm:sr-only">New project</span>
		</Button>
	{/snippet}
</PageHeader>

{#if fresh}
	<main class="min-h-0 flex-1 overflow-y-auto">
		<EmptyState variant="onboarding" title="Log your first run">
			{#snippet lead()}Runs appear here once they’re logged.{/snippet}
			<ol class="mt-8 grid gap-8">
				<li class="grid gap-3">
					<p><span class="mr-2 text-muted-foreground">1</span>Install the SDK:</p>
					<CopyBlock code="pip install wandb" />
				</li>
				<li class="grid gap-3">
					<p>
						<span class="mr-2 text-muted-foreground">2</span>Log in to
						<span class="font-mono text-[0.9em]">{data.flockUrl}</span> with this key:
					</p>
					<WandbKeyCreator />
				</li>
				<li class="grid gap-3">
					<p>
						<span class="mr-2 text-muted-foreground">3</span>Log a run to {data.org.display_name}:
					</p>
					<CopyBlock
						code={`import wandb\n\nrun = wandb.init(entity="${params.entity}", project="my-project")\nfor step in range(100):\n    run.log({"loss": 1 / (step + 1)})\nrun.finish()`}
					/>
				</li>
			</ol>
			<p class="mt-10 text-[13px] text-muted-foreground">
				Or
				<Button variant="link" class="h-auto p-0 text-[13px]" onclick={() => (creating = true)}>
					create an empty project
				</Button>.
			</p>
		</EmptyState>
	</main>
{:else}
	<main class="min-h-0 flex-1 overflow-y-auto pb-10">
		{#key JSON.stringify(data.query)}
			<LatestRuns
				first={() => data.feed}
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

<ProjectDialog bind:open={creating} entity={params.entity} />
