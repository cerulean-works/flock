<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';
	import DataTable, { type Column, ROW_LINK } from '#lib/components/DataTable.svelte';
	import InlinePager from '#lib/components/InlinePager.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import { FeedPages } from '#lib/data/feed-pages.svelte.ts';
	import { FeedPager } from '#lib/data/feed.svelte.ts';
	import type { FeedPage } from '#lib/types.ts';
	import { dateTime, duration, gpus, relativeTime } from '#lib/utils.ts';
	import { runHref } from '#lib/workspace/url.ts';

	interface Props {
		/** The newest runs, from the page load (polling refreshes it). */
		first: () => FeedPage;
		query: Record<string, string>;
		now: number;
		empty: string;
		/** Only the viewer's runs: the Owner column would repeat their name. */
		mine?: boolean;
	}

	let { first, query, now, empty, mine = false }: Props = $props();

	// svelte-ignore state_referenced_locally
	const pager = new FeedPager(first, query);

	// Narrower windows drop owner, GPUs and runtime, then state.
	const ALL_COLUMNS: Column[] = [
		{ id: 'run', label: 'Run' },
		{ id: 'project', label: 'Project' },
		{ id: 'state', label: 'State', size: 'fit', from: 'md' },
		{ id: 'owner', label: 'Owner', from: 'lg' },
		{ id: 'gpus', label: 'GPUs', size: 'fit', from: 'lg' },
		{ id: 'runtime', label: 'Runtime', from: 'lg', align: 'end' },
		{ id: 'started', label: 'Updated', align: 'end' }
	];
	const COLUMNS = $derived(mine ? ALL_COLUMNS.filter((c) => c.id !== 'owner') : ALL_COLUMNS);
	// One row per run, its latest state, 50 to a page.
	const pages = new FeedPages(pager, 50, (item) => !!item.run);
	// A page short of rows (merged events) loads more before it shows.
	$effect(() => {
		void pages.fill();
	});
</script>

<DataTable
	columns={COLUMNS}
	rows={pages.shown}
	key={(item) => item.id}
	name={(item) => item.run?.display_name ?? item.run?.id ?? item.id}
	label="Latest runs"
	empty={none}
	footer={pages.hasPrev || pages.hasNext ? more : undefined}
>
	{#snippet cell(item, column)}
		{@const run = item.run!}
		{#if column === 'run'}
			<div class="flex min-w-0 items-center gap-2">
				<a
					class={['truncate hover:text-link', ROW_LINK]}
					href={runHref(item.org, item.project, run.id)}
				>
					{run.display_name ?? run.id}
				</a>
				<!-- The state column is hidden on phones: its dot stands in for it. -->
				<span class="flex-none md:hidden"><StateBadge state={run.state} dotOnPhone /></span>
			</div>
		{:else if column === 'project'}
			<span class="block truncate text-muted-foreground">{item.project}</span>
		{:else if column === 'state'}
			<StateBadge state={run.state} />
		{:else if column === 'owner'}
			{#if item.actor}
				<span class="flex min-w-0 items-center gap-2">
					<Avatar name={item.actor.name} src={item.actor.avatar_url} class="size-4 text-[8px]" />
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
			<span class="text-muted-foreground" {@attach hint(dateTime(item.at))}>
				{relativeTime(item.at, now)}
			</span>
		{/if}
	{/snippet}
</DataTable>

{#snippet none()}{empty}{/snippet}
{#snippet more()}
	<InlinePager
		label="Run pages"
		from={pages.from}
		to={pages.from + pages.shown.length - 1}
		total={pages.total}
		onprev={pages.hasPrev ? pages.prev : undefined}
		onnext={pages.hasNext ? pages.next : undefined}
		pending={pager.loading}
		failed={pager.failed}
	/>
{/snippet}
