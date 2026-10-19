<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import * as Select from '#lib/components/ui/select/index.js';
	import { FEED_KINDS } from '#lib/data/feed.svelte.ts';
	import { withParam } from '#lib/workspace/url.ts';

	interface Props {
		/** The org's projects, to keep the feed to one of them. */
		projects: readonly string[];
		project: string | null;
		/** The kinds shown (`?kinds=`); leave out for a feed without that choice. */
		kinds?: string | null;
	}

	let { projects, project, kinds }: Props = $props();

	/** Select values can't be empty: this one stands for "no filter". */
	const ALL = '*';

	function set(key: string, value: string) {
		void goto(withParam(page.url, key, value === ALL ? null : value), {
			replace: true,
			reset: false
		});
	}
</script>

<div class="flex flex-wrap items-center gap-2">
	{#if kinds !== undefined}
		<Select.Root type="single" value={kinds ?? ALL} onValueChange={(v) => set('kinds', v)}>
			<Select.Trigger size="sm" class="min-w-36" aria-label="Kind of activity">
				{FEED_KINDS.find(([k]) => k === kinds)?.[1] ?? 'All activity'}
			</Select.Trigger>
			<Select.Content>
				<Select.Item value={ALL} label="All activity">All activity</Select.Item>
				{#each FEED_KINDS as [value, label] (value)}
					<Select.Item {value} {label}>{label}</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
	{/if}
	{#if projects.length > 1 || project}
		<Select.Root type="single" value={project ?? ALL} onValueChange={(v) => set('project', v)}>
			<Select.Trigger size="sm" class="max-w-56 min-w-36" aria-label="Project">
				<span class="truncate">{project ?? 'All projects'}</span>
			</Select.Trigger>
			<Select.Content>
				<Select.Item value={ALL} label="All projects">All projects</Select.Item>
				{#each projects as name (name)}
					<Select.Item value={name} label={name}>{name}</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
	{/if}
</div>
