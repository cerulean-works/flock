<script lang="ts">
	import Avatar from '#lib/components/Avatar.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { FeedPager } from '#lib/data/feed.svelte.ts';
	import type { FeedPage } from '#lib/types.ts';
	import { dateTime, duration, gpus, relativeTime } from '#lib/utils.ts';
	import { projectHref } from '#lib/workspace/url.ts';

	interface Props {
		/** The newest runs, from the page load (polling refreshes it). */
		first: () => FeedPage;
		query: Record<string, string>;
		now: number;
		empty: string;
	}

	let { first, query, now, empty }: Props = $props();

	// svelte-ignore state_referenced_locally
	const pager = new FeedPager(first, query);

	// Fixed columns so the header and rows line up: run, project, state, owner, GPUs, runtime,
	// started. Narrower windows drop owner, GPUs and runtime, then state.
	const GRID =
		'grid grid-cols-[minmax(0,1fr)_minmax(0,0.7fr)_72px] md:grid-cols-[minmax(0,1fr)_minmax(0,0.7fr)_112px_80px] lg:grid-cols-[minmax(0,1fr)_minmax(0,0.7fr)_112px_minmax(0,0.6fr)_96px_88px_80px] items-center gap-x-4 px-5';
	const MD = 'hidden md:block';
	const LG = 'hidden lg:block';
</script>

<div
	class={[
		GRID,
		'sticky top-0 z-1 h-10 border-b bg-background text-xs font-medium text-muted-foreground'
	]}
>
	<span>Run</span>
	<span>Project</span>
	<span class={MD}>State</span>
	<span class={LG}>Owner</span>
	<span class={LG}>GPUs</span>
	<span class={[LG, 'text-right']}>Runtime</span>
	<span class="text-right">Started</span>
</div>
<ul>
	{#each pager.items as item (item.id)}
		{@const run = item.run}
		{#if run}
			<li class={[GRID, 'relative min-h-14 border-b border-divider py-2.5 hover:bg-muted']}>
				<div class="min-w-0">
					<div class="flex items-center gap-2">
						<!-- The link covers the row. -->
						<a
							class="truncate font-medium after:absolute after:inset-0 hover:text-link"
							href={projectHref(item.org, item.project)}
						>
							{run.display_name ?? run.id}
						</a>
						{#if run.state === 'running' || run.state === 'preempting'}
							<!-- The state column is hidden on phones. -->
							<span
								class="size-1.5 flex-none animate-live rounded-full bg-state-running text-state-running motion-reduce:animate-none md:hidden"
								title="Running"
							></span>
						{/if}
					</div>
					{#if run.tags.length || run.job_type || run.group}
						<p class="mt-0.5 truncate text-[13px] text-muted-foreground">
							{[run.job_type, run.group, ...run.tags].filter(Boolean).join(' · ')}
						</p>
					{/if}
				</div>
				<span class="truncate text-muted-foreground">{item.project}</span>
				<span class={MD}><StateBadge state={run.state} /></span>
				<span class={[LG, 'min-w-0']}>
					{#if item.actor}
						<span class="flex min-w-0 items-center gap-2">
							<Avatar
								name={item.actor.name}
								src={item.actor.avatar_url}
								class="size-6 text-[10px]"
							/>
							<span class="truncate">{item.actor.name}</span>
						</span>
					{:else}
						<span class="text-faint">–</span>
					{/if}
				</span>
				<span class={[LG, 'truncate']}>
					{gpus(run.gpu_type, run.gpu_count) ?? '–'}
				</span>
				<span class={[LG, 'text-right text-muted-foreground tabular-nums']}>
					{duration(run.runtime)}
				</span>
				<span class="text-right text-muted-foreground" title={dateTime(item.at)}>
					{relativeTime(item.at, now)}
				</span>
			</li>
		{/if}
	{:else}
		<li class="border-b border-divider px-5 py-6 text-muted-foreground">{empty}</li>
	{/each}
</ul>
{#if pager.next}
	<div class="flex items-center gap-3 px-5 pt-4">
		<Button variant="outline" onclick={() => pager.more()} disabled={pager.loading}>
			{pager.loading ? 'Loading…' : 'Show more'}
		</Button>
		{#if pager.failed}<span class="text-[13px] text-destructive">Couldn’t load more</span>{/if}
	</div>
{/if}
