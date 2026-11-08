<script lang="ts">
	import FolderIcon from '@lucide/svelte/icons/folder';
	import type { ProjectInfo } from '#lib/types.ts';
	import { count, dateTime, relativeTime } from '#lib/utils.ts';
	import { projectHref } from '#lib/workspace/url.ts';

	interface Props {
		org: string;
		/** Its public projects, newest first. */
		projects: ProjectInfo[];
		now: number;
	}

	let { org, projects, now }: Props = $props();

	const ROW =
		'relative grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 border-b border-divider px-5 py-2.5 hover:bg-muted';
	// The link covers the row.
	const LINK =
		'flex min-w-0 items-center gap-2.5 font-medium after:absolute after:inset-0 hover:text-link [&_svg]:size-4 [&_svg]:flex-none [&_svg]:text-muted-foreground';
</script>

<!-- An org's page as people outside it see it: its public projects, and nothing else (D114).
     Public datasets aren't listed: they're reached by their URL (D115). -->
{#snippet heading(id: string, label: string, n: number)}
	<h2
		{id}
		class="flex h-10 items-center gap-2 border-b px-5 text-[13px] font-medium text-muted-foreground"
	>
		{label} <span class="font-normal text-faint tabular-nums">{n}</span>
	</h2>
{/snippet}

<section aria-labelledby="public-projects">
	{@render heading('public-projects', 'Projects', projects.length)}
	<ul>
		{#each projects as p (p.name)}
			<li class={ROW}>
				<div class="min-w-0">
					<a class={LINK} href={projectHref(org, p.name)}>
						<FolderIcon /> <span class="truncate">{p.name}</span>
					</a>
					{#if p.description}
						<p class="mt-0.5 truncate pl-6.5 text-[13px] text-muted-foreground">
							{p.description}
						</p>
					{/if}
				</div>
				<span class="text-right text-[13px] text-muted-foreground tabular-nums">
					{count(p.run_count, 'run')} ·
					<span title={dateTime(p.updated_at)}>{relativeTime(p.updated_at, now)}</span>
				</span>
			</li>
		{:else}
			<li class="border-b border-divider px-5 py-6 text-muted-foreground">No public projects.</li>
		{/each}
	</ul>
</section>
