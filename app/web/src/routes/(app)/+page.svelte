<script lang="ts">
	import BudgetPanel from '#lib/components/home/BudgetPanel.svelte';
	import FeedList from '#lib/components/home/FeedList.svelte';
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
				<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
					<h1 id="feed-title" class="font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">
						Activity
					</h1>
					<nav class="ml-auto flex gap-1 rounded-lg bg-muted p-0.5 text-[13px]" aria-label="Show">
						{#each TABS as [key, label] (key)}
							{@const on = (key === 'mine') === !!data.mine}
							<a
								class={[
									'rounded-md px-2.5 py-1 text-muted-foreground hover:text-foreground',
									on && 'bg-background text-foreground shadow-xs'
								]}
								href={withParam(page.url, 'feed', key || null)}
								data-sveltekit-noscroll
								data-sveltekit-replacestate
								aria-current={on ? 'page' : undefined}
							>
								{label}
							</a>
						{/each}
					</nav>
				</div>
				<p class="mt-1 text-muted-foreground">
					{data.mine ? 'Your runs' : 'Runs and projects'} in {data.org}
				</p>
				{#key data.mine}
					<FeedList
						first={() => data.feed!}
						query={data.query}
						now={data.now}
						empty={data.mine
							? 'You haven’t logged any runs here yet.'
							: 'Nothing has happened here yet.'}
					/>
				{/key}
			</section>

			<aside class="order-first min-w-0 xl:order-none xl:pt-3">
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
