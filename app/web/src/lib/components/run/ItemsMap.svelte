<script lang="ts">
	import ItemsSquare from './ItemsSquare.svelte';
	import type { ItemKeep, ItemsMap, ItemSuites } from '#lib/types.ts';

	/**
	 * Two runs' docs suite by suite (029): a square scatter plot per suite both runs recorded that
	 * holds accuracy tasks, lined up in a grid above the items, the one holding the list's task
	 * marked. The suites are listed first and drawn one at a time, so the squares don't hold up the
	 * list's own reads.
	 */
	interface Props {
		/** Run A's path under `/api/projects/`. */
		path: string;
		/** Run B as `entity/project/id`. */
		other: string;
		/** The task the list shows. */
		task: string;
		/** Told a clicked dot's task, cell, place in the cell's default order and id. */
		onopen: (task: string, keep: ItemKeep, index: number, id: string) => void;
	}

	let { path, other, task, onopen }: Props = $props();

	let suites = $state<ItemSuites['suites']>([]);
	let maps = $state<Record<string, ItemsMap>>({});

	// Refused or failed, a square stays empty: the list under it says why.
	$effect(() => {
		const controller = new AbortController();
		const signal = controller.signal;
		suites = [];
		maps = {};
		void (async () => {
			try {
				const asked = new URLSearchParams({ with: other });
				const res = await fetch(`/api/projects/${path}/items/suites?${asked}`, { signal });
				if (!res.ok) return;
				suites = ((await res.json()) as ItemSuites).suites;
				for (const { suite } of suites) {
					const query = new URLSearchParams({ suite, with: other });
					const answer = await fetch(`/api/projects/${path}/items/map?${query}`, { signal });
					if (answer.ok) maps[suite] = (await answer.json()) as ItemsMap;
				}
			} catch {
				// Aborted, or the network failed: the squares already drawn stay.
			}
		})();
		return () => controller.abort();
	});
</script>

{#if suites.length}
	<!-- Squares as wide as the column allows, as many to a row as fit; each keeps its shape. -->
	<div class="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3">
		{#each suites as { suite, tasks } (suite)}
			{#if maps[suite]}
				<ItemsSquare map={maps[suite]} current={tasks.includes(task)} {onopen} />
			{:else}
				<figure class="grid min-w-0 gap-1" aria-hidden="true">
					<figcaption class="truncate font-mono text-xs text-muted-foreground">{suite}</figcaption>
					<div class="aspect-square animate-pulse rounded-md bg-muted/40"></div>
				</figure>
			{/if}
		{/each}
	</div>
{/if}
