<script lang="ts">
	import BriefcaseIcon from '@lucide/svelte/icons/briefcase';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import TagIcon from '@lucide/svelte/icons/tag';
	import Avatar from '#lib/components/Avatar.svelte';
	import DataTable, { type Column, ROW_LINK } from '#lib/components/DataTable.svelte';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
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

	// Narrower windows drop owner, GPUs and runtime, then state.
	const COLUMNS: Column[] = [
		{ id: 'run', label: 'Run' },
		{ id: 'project', label: 'Project' },
		{ id: 'state', label: 'State', size: 'fit', from: 'md' },
		{ id: 'owner', label: 'Owner', from: 'lg' },
		{ id: 'gpus', label: 'GPUs', size: 'fit', from: 'lg' },
		{ id: 'runtime', label: 'Runtime', from: 'lg', align: 'end' },
		{ id: 'started', label: 'Started', align: 'end' }
	];
	const runs = $derived(pager.items.filter((item) => item.run));
</script>

<DataTable
	columns={COLUMNS}
	rows={runs}
	key={(item) => item.id}
	name={(item) => item.run?.display_name ?? item.run?.id ?? item.id}
	label="Latest runs"
	empty={none}
	footer={pager.next ? more : undefined}
>
	{#snippet cell(item, column)}
		{@const run = item.run!}
		{#if column === 'run'}
			<div class="flex items-center gap-2">
				<a
					class={['truncate font-medium hover:text-link', ROW_LINK]}
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
				<MetaRow class="mt-0.5 flex-nowrap gap-x-3 overflow-hidden">
					{#if run.job_type}<MetaItem icon={BriefcaseIcon} title="Job type">{run.job_type}</MetaItem
						>{/if}
					{#if run.group}<MetaItem icon={LayersIcon} title="Group">{run.group}</MetaItem>{/if}
					{#if run.tags.length}<MetaItem icon={TagIcon} title="Tags">{run.tags.join(', ')}</MetaItem
						>{/if}
				</MetaRow>
			{/if}
		{:else if column === 'project'}
			<span class="block truncate text-muted-foreground">{item.project}</span>
		{:else if column === 'state'}
			<StateBadge state={run.state} />
		{:else if column === 'owner'}
			{#if item.actor}
				<span class="flex min-w-0 items-center gap-2">
					<Avatar name={item.actor.name} src={item.actor.avatar_url} class="size-6 text-[10px]" />
					<span class="truncate">{item.actor.name}</span>
				</span>
			{:else}
				<span class="text-faint">–</span>
			{/if}
		{:else if column === 'gpus'}
			<span class="block truncate">{gpus(run.gpu_type, run.gpu_count) ?? '–'}</span>
		{:else if column === 'runtime'}
			<span class="text-muted-foreground">{duration(run.runtime)}</span>
		{:else}
			<span class="text-muted-foreground" title={dateTime(item.at)}>
				{relativeTime(item.at, now)}
			</span>
		{/if}
	{/snippet}
</DataTable>

{#snippet none()}{empty}{/snippet}
{#snippet more()}
	<Button variant="outline" onclick={() => pager.more()} disabled={pager.loading}>
		{pager.loading ? 'Loading…' : 'Show more'}
	</Button>
	{#if pager.failed}<span class="text-[13px] text-destructive">Couldn’t load more</span>{/if}
{/snippet}
