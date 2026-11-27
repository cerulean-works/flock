<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import * as Select from '#lib/components/ui/select/index.js';
	import { RUN_STATES } from '#lib/data/feed.svelte.ts';
	import { withParam } from '#lib/workspace/url.ts';

	interface Props {
		/** The org's projects, to keep the feed to one of them. */
		projects: readonly string[];
		project: string | null;
		/** The runs' state shown (`?state=`); leave out for a list without that choice. */
		state?: string | null;
	}

	let { projects, project, state }: Props = $props();

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
	{#if state !== undefined}
		<Select.Root type="single" value={state ?? ALL} onValueChange={(v) => set('state', v)}>
			<Select.Trigger class={QUIET} aria-label="State">
				{RUN_STATES.find((s) => s.value === state)?.label ?? 'All states'}
			</Select.Trigger>
			<Select.Content side="bottom" align="start">
				<Select.Item value={ALL} label="All states">All states</Select.Item>
				{#each RUN_STATES as s (s.value)}
					<Select.Item value={s.value} label={s.label}>{s.label}</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
	{/if}
	{#if projects.length > 1 || project}
		<Select.Root type="single" value={project ?? ALL} onValueChange={(v) => set('project', v)}>
			<Select.Trigger class={[QUIET, 'max-w-56']} aria-label="Project">
				<span class="truncate">{project ?? 'All projects'}</span>
			</Select.Trigger>
			<Select.Content side="bottom" align="start">
				<Select.Item value={ALL} label="All projects">All projects</Select.Item>
				{#each projects as name (name)}
					<Select.Item value={name} label={name}>{name}</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
	{/if}
</div>
