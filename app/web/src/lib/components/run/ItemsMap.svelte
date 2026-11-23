<script lang="ts">
	import ItemsSquare from './ItemsSquare.svelte';
	import type { ItemKeep, ItemSuites } from '#lib/types.ts';

	/**
	 * Two runs' docs suite by suite (029): one row of equal squares, a small scatter plot per suite
	 * both runs recorded that holds accuracy tasks, scrolling sideways when they don't fit. The one
	 * holding the list's task is marked. Clicking a square's quadrant shows that suite and cell in
	 * the list; the pointer on a quadrant lifts the list's button for its cell, and the pointer on a
	 * button lifts the quadrant in the marked square.
	 */
	interface Props {
		/** Run A's path under `/api/projects/`. */
		path: string;
		/** Run B as `entity/project/id`. */
		other: string;
		/** The task the list shows. */
		task: string;
		/** The cells' names, as the list's keeps label them. */
		keeps: { keep: ItemKeep; label: string }[];
		/** The cell lifted from the list's buttons, which the marked square lifts too. */
		lifted: ItemKeep | null;
		/** Told the cell under the pointer in any square, or null when it leaves. */
		onlift: (keep: ItemKeep | null) => void;
		/** Told a clicked square's tasks and cell. */
		onpick: (tasks: string[], keep: ItemKeep) => void;
	}

	let { path, other, task, keeps, lifted, onlift, onpick }: Props = $props();

	let suites = $state<ItemSuites['suites'] | null>(null);
	let loading = $state(false);

	// Refused or failed, the row stays away: the list under it says why. A pair's first visit
	// computes every suite (tens of seconds on a whole bank); Flock keeps the answer after.
	$effect(() => {
		const controller = new AbortController();
		suites = null;
		loading = true;
		const asked = new URLSearchParams({ with: other });
		fetch(`/api/projects/${path}/items/suites?${asked}`, { signal: controller.signal })
			.then(async (res) => {
				const body = res.ok ? ((await res.json()) as ItemSuites) : null;
				if (controller.signal.aborted) return;
				suites = body?.suites ?? null;
				loading = false;
			})
			.catch(() => {
				if (!controller.signal.aborted) loading = false;
			});
		return () => controller.abort();
	});
</script>

{#if suites?.length}
	<div class="flex gap-3 overflow-x-auto pb-1">
		{#each suites as suite (suite.suite)}
			<ItemsSquare
				{suite}
				current={suite.tasks.includes(task)}
				{lifted}
				{keeps}
				{onlift}
				onpick={(keep) => onpick(suite.tasks, keep)}
			/>
		{/each}
	</div>
{:else if loading}
	<!-- The row's place, kept while the suites are computed, so the list doesn't jump. -->
	<div class="flex gap-3 overflow-hidden pb-1" aria-hidden="true">
		{#each Array.from({ length: 8 }, (_, i) => i) as i (i)}
			<div class="size-28 flex-none animate-pulse rounded-md bg-muted/40"></div>
		{/each}
	</div>
{/if}
