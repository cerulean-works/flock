<script lang="ts">
	import ItemsSquare from './ItemsSquare.svelte';
	import type { ItemKeep, ItemSuites } from '#lib/types.ts';

	/**
	 * Two runs' docs suite by suite (029): one row of equal squares, a small scatter plot per suite
	 * both runs recorded that holds accuracy tasks, scrolling sideways when they don't fit. The one
	 * holding the list's task is marked (the last clicked, when several do). Clicking a square's
	 * quadrant shows that suite and cell in
	 * the list; the pointer on a quadrant lifts the list's button for its cell, and the pointer on a
	 * button lifts the quadrant in the marked square. A clicked dot opens its doc in the list.
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
		/** Told a clicked dot's task, cell, place in the cell's default order and id. */
		onopen: (task: string, keep: ItemKeep, index: number, id: string) => void;
	}

	let { path, other, task, keeps, lifted, onlift, onpick, onopen }: Props = $props();

	let suites = $state<ItemSuites['suites'] | null>(null);
	// The square last clicked. Suites share tasks (`base_easy:qa_rc` holds all of MMLU), so one
	// square is marked: the one clicked while it holds the task shown, else the first that does.
	let clicked = $state<string | null>(null);
	const marked = $derived.by(() => {
		const holding = (suites ?? []).filter((s) => s.tasks.includes(task));
		return (holding.find((s) => s.suite === clicked) ?? holding[0])?.suite ?? null;
	});
	let loading = $state(false);
	let width = $state(0);

	// As many squares as fit at about 112 px, sized so they fill the text's width exactly, a gap
	// of 12 px between them: no square is cut at the edge until the row scrolls.
	const GAP = 12;
	const side = $derived.by(() => {
		const room = width - 8; // inside the row's 4 px padding, which keeps the ring whole
		const fit = Math.max(1, Math.floor((room + GAP) / (112 + GAP)));
		return room > 0 ? Math.floor((room + GAP) / fit - GAP) : 112;
	});

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
	<!-- Within the text's width, padded so the marked square's ring isn't clipped by the scrolling
	     box. A scroll comes to rest on a square's edge. -->
	<div
		class="-mx-1 flex snap-x snap-proximity scroll-px-1 gap-3 overflow-x-auto p-1"
		bind:clientWidth={width}
	>
		{#each suites as suite (suite.suite)}
			<ItemsSquare
				{suite}
				{side}
				current={suite.suite === marked}
				{lifted}
				{keeps}
				{onlift}
				onpick={(keep) => {
					clicked = suite.suite;
					onpick(suite.tasks, keep);
				}}
				onopen={(...dot) => {
					clicked = suite.suite;
					onopen(...dot);
				}}
			/>
		{/each}
	</div>
{:else if loading}
	<!-- The row's place, kept while the suites are computed, so the list doesn't jump. -->
	<div class="flex gap-3 overflow-hidden p-1" aria-hidden="true">
		{#each Array.from({ length: 8 }, (_, i) => i) as i (i)}
			<div class="size-28 flex-none animate-pulse rounded-md bg-muted/40"></div>
		{/each}
	</div>
{/if}
