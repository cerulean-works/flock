<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import CpuIcon from '@lucide/svelte/icons/cpu';
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import TagIcon from '@lucide/svelte/icons/tag';
	import TimerIcon from '@lucide/svelte/icons/timer';
	import UserIcon from '@lucide/svelte/icons/user';
	import BriefcaseIcon from '@lucide/svelte/icons/briefcase';
	import Avatar from '#lib/components/Avatar.svelte';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import type { FeedItem } from '#lib/types.ts';
	import { dateTime, duration, gpus, relativeTime } from '#lib/utils.ts';
	import { projectHref } from '#lib/workspace/url.ts';

	let { item, now }: { item: FeedItem; now: number } = $props();

	const ENDED: Partial<Record<FeedItem['kind'], [string, string]>> = {
		run_finished: ['finished', 'text-state-finished'],
		run_failed: ['failed', 'text-state-failed'],
		run_crashed: ['crashed', 'text-state-failed']
	};

	const href = $derived(projectHref(item.org, item.project));
	const run = $derived(item.run);
	const ended = $derived(ENDED[item.kind]);
	const hardware = $derived(run && gpus(run.gpu_type, run.gpu_count));
</script>

<!-- A one-line entry (a project created without a description) centres its icon on that line. -->
<li
	class={[
		'flex gap-3 border-b border-divider py-3.5',
		item.kind === 'project_created' && !item.description && 'items-center'
	]}
>
	{#if item.kind === 'project_created'}
		<span
			class="grid size-7 flex-none place-items-center rounded-full bg-muted text-muted-foreground"
		>
			<FolderPlusIcon class="size-3.5" />
		</span>
	{:else}
		<span {@attach hint(item.actor?.name)}
			><Avatar name={item.actor?.name ?? '?'} src={item.actor?.avatar_url} /></span
		>
	{/if}

	<div class="min-w-0 flex-1">
		<div class="flex items-baseline gap-3">
			<p class="min-w-0 flex-1 leading-snug">
				{#if item.kind === 'project_created'}
					Project <a class="font-medium hover:text-link" {href}>{item.project}</a> created
				{:else if run}
					{@const name = run.display_name ?? run.id}
					{#if ended}
						<a class="font-medium hover:text-link" {href}>{name}</a>
						<span class={ended[1]}>{ended[0]}</span> in
						<a class="text-muted-foreground hover:text-link" {href}>{item.project}</a>
					{:else if item.actor}
						<span class="font-medium">{item.actor.name}</span> started
						<a class="font-medium hover:text-link" {href}>{name}</a> in
						<a class="text-muted-foreground hover:text-link" {href}>{item.project}</a>
					{:else}
						<a class="font-medium hover:text-link" {href}>{name}</a> started in
						<a class="text-muted-foreground hover:text-link" {href}>{item.project}</a>
					{/if}
				{/if}
			</p>
			<time
				class="flex-none text-[13px] text-muted-foreground"
				datetime={new Date(item.at * 1000).toISOString()}
				{@attach hint(dateTime(item.at))}
			>
				{relativeTime(item.at, now)}
			</time>
		</div>

		{#if item.kind === 'project_created'}
			{#if item.description}
				<p class="mt-1 line-clamp-2 text-[13px] text-muted-foreground">{item.description}</p>
			{/if}
		{:else if run}
			<MetaRow class="mt-1.5 gap-x-3">
				{#if item.kind === 'run_started'}<StateBadge state={run.state} />{/if}
				{#if ended && item.actor}<MetaItem icon={UserIcon}>{item.actor.name}</MetaItem>{/if}
				{#if hardware}<MetaItem icon={CpuIcon} title="GPUs">{hardware}</MetaItem>{/if}
				<MetaItem icon={TimerIcon} title="Runtime">{duration(run.runtime)}</MetaItem>
				{#if run.job_type}<MetaItem icon={BriefcaseIcon} title="Job type">{run.job_type}</MetaItem
					>{/if}
				{#if run.group}<MetaItem icon={LayersIcon} title="Group">{run.group}</MetaItem>{/if}
				{#if run.tags.length}
					<MetaItem icon={TagIcon} title="Tags: {run.tags.join(', ')}">
						{run.tags.slice(0, 4).join(', ')}{run.tags.length > 4 ? ` +${run.tags.length - 4}` : ''}
					</MetaItem>
				{/if}
			</MetaRow>
		{/if}
	</div>
</li>
