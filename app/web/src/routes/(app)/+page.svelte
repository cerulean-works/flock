<script lang="ts">
	import BudgetPanel from '#lib/components/home/BudgetPanel.svelte';
	import FeedFilters from '#lib/components/home/FeedFilters.svelte';
	import FeedList from '#lib/components/home/FeedList.svelte';
	import SegmentedNav from '#lib/components/SegmentedNav.svelte';
	import { page } from '$app/state';
	import { pollFeed } from '#lib/data/feed.svelte.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	$effect(() => pollFeed());

	const TABS = [
		['', 'Everything'],
		['mine', 'My runs']
	] as const;
</script>

<svelte:head>
	<title>Home · Flock</title>
</svelte:head>

<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
	{#if data.org && data.feed && data.budget}
		<div class="mx-auto grid max-w-280 gap-x-12 gap-y-10 pt-8 xl:grid-cols-[minmax(0,1fr)_320px]">
			<section aria-labelledby="feed-title" class="min-w-0">
				<!-- A 40px band, shared with the budget's heading so the two columns start level. -->
				<div class="flex min-h-10 flex-wrap items-center gap-x-4 gap-y-2">
					<h1 id="feed-title" class="font-serif text-[34px] leading-10 tracking-[-0.02em]">
						Activity
					</h1>
					<div class="ml-auto flex flex-wrap items-center gap-2">
						<FeedFilters
							projects={data.shell.projects.map((p) => p.name)}
							project={data.project ?? null}
							kinds={data.kinds}
						/>
						<SegmentedNav
							label="Show"
							items={TABS.map(([key, label]) => ({
								href: withParam(page.url, 'feed', key || null),
								label,
								current: (key === 'mine') === !!data.mine
							}))}
						/>
					</div>
				</div>
				<p class="mt-1 text-muted-foreground">
					{data.mine ? 'Your runs' : 'Runs and projects'} in {data.project
						? `${data.shell.orgName} / ${data.project}`
						: data.shell.orgName}
				</p>
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

			<aside class="order-first min-w-0 xl:order-none">
				<BudgetPanel budget={data.budget} thisMonth={data.thisMonth} admin={data.shell.admin} />
			</aside>
		</div>
	{:else}
		<div class="mx-auto mt-[12vh] max-w-160 font-serif text-[19px] leading-normal">
			<h1 class="mb-4 text-[40px] leading-tight tracking-[-0.02em]">No organization yet</h1>
			<p>Ask an admin to invite you.</p>
		</div>
	{/if}
</main>
