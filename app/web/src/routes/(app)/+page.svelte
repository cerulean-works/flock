<script lang="ts">
	import UserIcon from '@lucide/svelte/icons/user';
	import BudgetPanel from '#lib/components/home/BudgetPanel.svelte';
	import FeedFilters from '#lib/components/home/FeedFilters.svelte';
	import FeedList from '#lib/components/home/FeedList.svelte';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { hint } from '#lib/hint.svelte.ts';
	import { page } from '$app/state';
	import { pollFeed } from '#lib/data/feed.svelte.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	$effect(() => pollFeed());

	const running = $derived(data.shell.projects.reduce((n, p) => n + p.active_run_count, 0));
</script>

<svelte:head>
	<title>Home · Flock</title>
</svelte:head>

{#if data.org && data.feed && data.budget}
	<Page width="full">
		<PageHeader title="Home" live={running}>
			<!-- No view tabs here: the filters sit in the header row, one row as in Linear's Inbox. -->
			{#snippet actions()}
				<BudgetPanel
					budget={data.budget}
					thisMonth={data.thisMonth}
					admin={data.shell.access === 'admin'}
					class="max-sm:hidden"
				/>
				<FeedFilters
					projects={data.shell.projects.map((p) => p.name)}
					project={data.project ?? null}
					kinds={data.kinds}
				/>
				<Button
					variant={data.mine ? 'quiet-on' : 'quiet'}
					size="bar"
					href={withParam(page.url, 'feed', data.mine ? null : 'mine')}
					aria-pressed={!!data.mine}
					aria-label="My runs"
					data-sveltekit-replacestate
					{@attach hint(data.mine ? 'Show everyone’s runs' : 'Show only the runs you logged')}
				>
					<UserIcon /> <span class="max-sm:hidden">My runs</span>
				</Button>
			{/snippet}
		</PageHeader>
		<main class="min-h-0 flex-1 overflow-y-auto px-5 pt-4 pb-16">
			<!-- The feed takes the width; the budget is a line in the header (above the feed on
			     phones, where the header has no room). -->
			<BudgetPanel
				budget={data.budget}
				thisMonth={data.thisMonth}
				admin={data.shell.access === 'admin'}
				class="mb-2 -ml-2 sm:hidden"
			/>
			<section aria-label="Activity" class="min-w-0">
				{#key JSON.stringify(data.query)}
					<FeedList
						first={() => data.feed!}
						query={data.query}
						now={data.now}
						empty={data.kinds || data.project
							? 'Nothing like that has happened here yet.'
							: data.mine
								? 'You haven’t logged any runs here yet.'
								: 'Nothing has happened here yet.'}
					/>
				{/key}
			</section>
		</main>
	</Page>
{:else}
	<Page width="home">
		<div class="mx-auto mt-[12vh] max-w-160 text-[17px] leading-[1.45]">
			<h1 class="mb-4 text-[40px] leading-tight tracking-[-0.02em]">No organization yet</h1>
			<p>Ask an admin to invite you.</p>
		</div>
	</Page>
{/if}
