<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import UserIcon from '@lucide/svelte/icons/user';
	import { hint } from '#lib/hint.svelte.ts';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import BudgetPanel from '#lib/components/home/BudgetPanel.svelte';
	import FeedFilters from '#lib/components/home/FeedFilters.svelte';
	import LatestRuns from '#lib/components/home/LatestRuns.svelte';
	import PublicOrg from '#lib/components/home/PublicOrg.svelte';
	import ListPage from '#lib/components/ListPage.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
	import WandbKeyCreator from '#lib/components/WandbKeyCreator.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { page } from '$app/state';
	import { pollFeed } from '#lib/data/feed.svelte.ts';
	import { wandbSnippet } from '#lib/snippets.ts';
	import { orgHref, withParam } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	$effect(() => pollFeed());

	// Nothing logged yet: how to log the first run, instead of an empty table.
	const fresh = $derived(!data.mine && !data.project && !data.state && !data.feed?.items.length);

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
	<Button
		variant="quiet"
		size="icon-bar"
		href={orgHref(params.entity)}
		aria-label="Settings"
		{@attach hint('Settings and members')}
	>
		<SettingsIcon />
	</Button>
	<Button
		variant="quiet"
		size="icon-bar"
		onclick={() => (creating = true)}
		aria-label="New project"
		{@attach hint('New project')}
	>
		<PlusIcon />
	</Button>
{/snippet}

<!-- The feed's own controls, under the title once there are runs to show. -->
{#snippet feedControls()}
	<FeedFilters
		projects={data.shell.projects.map((p) => p.name)}
		project={data.project}
		state={data.state}
	/>
	<Button
		variant={data.mine ? 'quiet-on' : 'quiet'}
		size="bar"
		href={withParam(page.url, 'runs', data.mine ? null : 'mine')}
		aria-pressed={data.mine}
		data-sveltekit-replacestate
		{@attach hint(data.mine ? 'Show everyone’s runs' : 'Show only the runs you logged')}
	>
		<UserIcon /> <span class="max-sm:sr-only">My runs</span>
	</Button>
{/snippet}

<ListPage
	title={data.outside ? data.org.display_name : 'Runs'}
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
		{#if data.budget}
			<BudgetPanel
				budget={data.budget}
				thisMonth={data.thisMonth}
				admin={data.shell.access === 'admin'}
				class="mx-3 mt-2 sm:hidden"
			/>
		{/if}
		{#key JSON.stringify(data.query)}
			<LatestRuns
				first={() => feed}
				query={data.query}
				now={data.now}
				mine={data.mine}
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
