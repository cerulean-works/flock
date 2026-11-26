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

	// Quiet view-bar controls: borderless until hovered, muted until set.
	const QUIET =
		'h-7! gap-1 border-0 bg-transparent px-2 text-[13px] text-muted-foreground shadow-none hover:bg-muted hover:text-foreground data-[state=open]:bg-muted dark:bg-transparent dark:hover:bg-muted';

	/** Select values can't be empty: this one stands for "no filter". */
	const ALL = '*';

	function set(key: string, value: string) {
		void goto(withParam(page.url, key, value === ALL ? null : value), {
			replace: true,
			reset: false
		});
	}
</script>

<div class="flex items-center gap-1">
	{#if kinds !== undefined}
		<Select.Root type="single" value={kinds ?? ALL} onValueChange={(v) => set('kinds', v)}>
			<Select.Trigger class={QUIET} aria-label="Kind of activity">
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
			<Select.Trigger class={[QUIET, 'max-w-56']} aria-label="Project">
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
