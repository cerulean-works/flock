<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import Avatar from '#lib/components/Avatar.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import type { FeedItem } from '#lib/types.ts';
	import { dateTime, duration, gpus, relativeTime } from '#lib/utils.ts';
	import { projectHref, runHref } from '#lib/workspace/url.ts';

	let { item, now }: { item: FeedItem; now: number } = $props();

	const project = $derived(projectHref(item.org, item.project));
	const run = $derived(item.run);
	/** What the row leaves out, for its hover: hardware, job type, group and tags. */
	const details = $derived(
		run
			? [
					gpus(run.gpu_type, run.gpu_count),
					run.job_type,
					run.group,
					run.tags.length ? run.tags.join(', ') : null
				]
					.filter(Boolean)
					.join(' · ')
			: null
	);
</script>

<!-- One 36px line per run, showing its latest state; a new project is a line of its own. -->
<li class="flex h-9 items-center gap-3 border-b border-divider text-[13px]">
	{#if item.kind === 'project_created'}
		<span class="grid size-5 flex-none place-items-center text-muted-foreground">
			<FolderPlusIcon class="size-4" />
		</span>
		<p class="min-w-0 flex-1 truncate">
			<a class="hover:text-link" href={project} {@attach hint(item.description)}>{item.project}</a>
			<span class="text-muted-foreground">created</span>
		</p>
	{:else if run}
		<span class="flex-none" {@attach hint(item.actor?.name)}>
			<Avatar
				name={item.actor?.name ?? '?'}
				src={item.actor?.avatar_url}
				class="size-5 text-[9px]"
			/>
		</span>
		<p class="flex min-w-0 flex-1 items-baseline gap-2">
			<a
				class="min-w-0 truncate hover:text-link"
				href={runHref(item.org, item.project, run.id)}
				{@attach hint(details)}
			>
				{run.display_name ?? run.id}
			</a>
			<a
				class="min-w-0 flex-none truncate text-muted-foreground hover:text-link max-sm:hidden"
				href={project}
			>
				{item.project}
			</a>
		</p>
		<span class="w-24 flex-none max-sm:w-auto"><StateBadge state={run.state} /></span>
		<span class="w-16 flex-none text-right text-muted-foreground tabular-nums max-md:hidden">
			{duration(run.runtime)}
		</span>
	{/if}
	<time
		class="w-14 flex-none text-right text-muted-foreground tabular-nums"
		datetime={new Date(item.at * 1000).toISOString()}
		{@attach hint(dateTime(item.at))}
	>
		{relativeTime(item.at, now)}
	</time>
</li>
