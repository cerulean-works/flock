<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';
	import DataTable, { type Column, ROW_LINK } from '#lib/components/DataTable.svelte';
	import InlinePager from '#lib/components/InlinePager.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import { FEED_SIZES, FeedPages } from '#lib/data/feed-pages.svelte.ts';
	import { FeedPager } from '#lib/data/feed.svelte.ts';
	import type { FeedPage } from '#lib/types.ts';
	import type { Snippet } from 'svelte';
	import { dateTime, duration, gpus, relativeTime } from '#lib/utils.ts';
	import { runHref } from '#lib/workspace/url.ts';
	import { keyboard } from '#lib/keys.svelte.ts';

	interface Props {
		/** The newest runs, from the page load (polling refreshes it). */
		first: () => FeedPage;
		query: Record<string, string>;
		now: number;
		empty: string;
		/** Only the viewer's runs: the phone's avatar would repeat their face. */
		mine?: boolean;
		/** A header cell's content (the Owner, Project and State filters). */
		head?: Snippet<[Column]>;
	}

	let { first, query, now, empty, mine = false, head }: Props = $props();

	// svelte-ignore state_referenced_locally
	const pager = new FeedPager(first, query);

	// Who, then which run, then what it is; narrower windows drop group and tags, then the job
	// type, GPUs and runtime, then the state, then the project. The run's name keeps its width; project, group and tags give way. On a
	// phone the owner's avatar leads the run's cell.
	const ALL_COLUMNS: Column[] = [
		{ id: 'owner', label: 'Owner', size: 'fit', from: 'sm' },
		{ id: 'run', label: 'Run', size: 'fit' },
		{ id: 'project', label: 'Project', from: 'sm' },
		{ id: 'state', label: 'State', size: 'fit', from: 'md' },
		{ id: 'group', label: 'Group', from: '2xl' },
		{ id: 'job', label: 'Job type', size: 'fit', from: 'xl' },
		{ id: 'tags', label: 'Tags', from: '2xl' },
		{ id: 'gpus', label: 'GPUs', size: 'fit', from: 'lg' },
		{ id: 'runtime', label: 'Runtime', from: 'lg', align: 'end' },
		{ id: 'started', label: 'Updated', align: 'end' }
	];
	// The Owner column stays even on one person's runs: its header is where the filter changes.
	const COLUMNS = ALL_COLUMNS;
	// One row per run, its latest state.
	const pages = new FeedPages(pager, (item) => !!item.run);
	// A page short of rows (merged events) loads more before it shows.
	$effect(() => {
		void pages.fill();
	});
	// ⌘K jumps to the runs listed, each with its project.
	$effect(() => {
		keyboard.runs = pages.shown
			.filter((item) => item.run)
			.map((item) => ({
				label: item.run!.display_name ?? item.run!.id,
				href: runHref(item.org, item.project, item.run!.id),
				project: item.project
			}));
		return () => (keyboard.runs = []);
	});
</script>

<DataTable
	columns={COLUMNS}
	rows={pages.shown}
	key={(item) => item.id}
	name={(item) => item.run?.display_name ?? item.run?.id ?? item.id}
	label="Latest runs"
	{head}
	empty={none}
>
	{#snippet cell(item, column)}
		{@const run = item.run!}
		{#if column === 'run'}
			<div class="flex min-w-0 items-center gap-2">
				{#if item.actor && !mine}
					<Avatar
						name={item.actor.name}
						src={item.actor.avatar_url}
						class="size-4 flex-none text-[8px] sm:hidden"
					/>
				{/if}
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
				<span class="flex min-w-0 items-center gap-2" {@attach hint(item.actor.name)}>
					<Avatar
						name={item.actor.name}
						src={item.actor.avatar_url}
						class="size-4 flex-none text-[8px]"
					/>
					<span class="truncate">{item.actor.name}</span>
				</span>
			{:else}
				<span class="text-faint">–</span>
			{/if}
		{:else if column === 'group'}
			<span class="block truncate text-muted-foreground" {@attach hint(run.group ?? undefined)}
				>{run.group ?? ''}</span
			>
		{:else if column === 'job'}
			<span class="block truncate text-muted-foreground">{run.job_type ?? ''}</span>
		{:else if column === 'tags'}
			<span
				class="block truncate text-muted-foreground"
				{@attach hint(run.tags.length ? run.tags.join(', ') : undefined)}
				>{run.tags.join(', ')}</span
			>
		{:else if column === 'gpus'}
			<span class="block truncate text-muted-foreground"
				>{gpus(run.gpu_type, run.gpu_count) ?? '–'}</span
			>
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
{#if pages.hasPrev || pages.hasNext || pages.rows.length > FEED_SIZES[0]}
	<!-- Pinned at the scroll's foot, at the runs table pager's place (16px in, 14px up: the list
	     page's 40px bottom room is the scroll's padding, which sticky keeps clear of). -->
	<div class="sticky bottom-[-1.625rem] z-5 mt-3 ml-4 w-fit">
		<InlinePager
			label="Run pages"
			from={pages.from}
			to={pages.from + pages.shown.length - 1}
			total={pages.total}
			onprev={pages.hasPrev ? pages.prev : undefined}
			onnext={pages.hasNext ? pages.next : undefined}
			pending={pager.loading}
			failed={pager.failed}
			size={pages.size}
			sizes={FEED_SIZES}
			sizeLabel="Runs per page"
			onsize={pages.resize}
		/>
	</div>
{/if}
