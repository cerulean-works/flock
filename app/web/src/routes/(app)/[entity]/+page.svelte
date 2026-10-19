<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import ApiKeyCreator from '#lib/components/ApiKeyCreator.svelte';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import LatestRuns from '#lib/components/home/LatestRuns.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
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
	const fresh = $derived(!data.mine && !data.feed.items.length);

	const TABS = [
		['', 'Everyone'],
		['mine', 'Mine']
	] as const;

	let creating = $state(false);
</script>

<svelte:head>
	<title>Runs · {params.entity}</title>
</svelte:head>

<div class="flex flex-none flex-wrap items-baseline gap-x-4 gap-y-1 px-5 pt-5 pb-4">
	<h1 class="font-serif text-[34px] leading-[1.15] font-normal tracking-[-0.02em]">
		{params.entity}
	</h1>
	<p class="order-last basis-full text-[13px] text-muted-foreground sm:order-none sm:basis-auto">
		{count(data.org.project_count, 'project')} · {count(data.org.member_count, 'member')}
		{#if running}
			· <span class="text-state-running">{count(running, 'run')} running</span>
		{/if}
	</p>
	<div class="ml-auto flex gap-2 self-center">
		<Button variant="ghost" href={orgHref(params.entity)}>
			<SettingsIcon /> Settings
		</Button>
		<Button variant="outline" onclick={() => (creating = true)}>
			<PlusIcon /> New project
		</Button>
	</div>
</div>

{#if fresh}
	<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
		<div class="mx-auto mt-[6vh] max-w-160">
			<h2 class="font-serif text-[28px] leading-tight tracking-[-0.01em]">Log your first run</h2>
			<p class="mt-2 font-serif text-[19px] leading-normal">
				Runs appear here once they’re logged.
			</p>
			<ol class="mt-8 grid gap-8">
				<li class="grid gap-3">
					<p>
						<span class="mr-2 text-muted-foreground">1</span>Install the SDK and log in to Flock:
					</p>
					<CopyBlock code="pip install wandb" />
					<ApiKeyCreator />
				</li>
				<li class="grid gap-3">
					<p><span class="mr-2 text-muted-foreground">2</span>Log a run to {params.entity}:</p>
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
		</div>
	</main>
{:else}
	<div class="flex flex-none items-center gap-3 px-5 pb-3">
		<h2 class="text-[13px] font-medium">Latest runs</h2>
		<nav class="ml-auto flex gap-1 rounded-lg bg-muted p-0.5 text-[13px]" aria-label="Show">
			{#each TABS as [key, label] (key)}
				{@const on = (key === 'mine') === data.mine}
				<a
					class={[
						'rounded-md px-2.5 py-1 text-muted-foreground hover:text-foreground',
						on && 'bg-background text-foreground shadow-xs'
					]}
					href={withParam(page.url, 'runs', key || null)}
					data-sveltekit-noscroll
					data-sveltekit-replacestate
					aria-current={on ? 'page' : undefined}
				>
					{label}
				</a>
			{/each}
		</nav>
	</div>
	<main class="min-h-0 flex-1 overflow-y-auto pb-10">
		{#key data.mine}
			<LatestRuns
				first={() => data.feed}
				query={data.query}
				now={data.now}
				empty={data.mine ? 'You haven’t logged any runs here yet.' : 'No runs yet.'}
			/>
		{/key}
	</main>
{/if}

<ProjectDialog bind:open={creating} entity={params.entity} />
