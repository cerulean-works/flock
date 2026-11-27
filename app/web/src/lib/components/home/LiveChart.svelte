<script lang="ts">
	import { untrack } from 'svelte';
	import { fade } from 'svelte/transition';
	import { hint } from '#lib/hint.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';
	import Spark from '#lib/components/home/Spark.svelte';
	import Tile from '#lib/components/home/Tile.svelte';
	import { reading, runCurves, type Curve } from '#lib/home/curves.ts';
	import { mainMetric } from '#lib/home/pins.svelte.ts';
	import type { HomeRun } from '#lib/home/runs.ts';
	import { formatNumber } from '#lib/utils.ts';
	import { runHref } from '#lib/workspace/url.ts';

	interface Props {
		/** Live runs, the viewer's first. */
		runs: HomeRun[];
		/** The viewer, named "You" on their own runs. */
		me: number | null;
		/** A row of the Runs list under the pointer (`project/id`): it takes the first place. */
		hovered: string | null;
		/** Told the runs shown now, so the Runs list can light their rows. */
		onshow?: (keys: string[]) => void;
		index: number;
	}

	let { runs, me, hovered, onshow, index }: Props = $props();

	/** Runs side by side, and seconds a pair shows before the next. */
	const PER = 2;
	const ROTATE = 6;

	const keyOf = (r: HomeRun) => `${r.project}/${r.id}`;
	let curves = $state<Record<string, { metric: string; curve: Curve }>>({});
	$effect(() => {
		const list = runs;
		const controller = new AbortController();
		const readAll = () => {
			for (const r of list) {
				const known = untrack(() => curves[keyOf(r)]?.metric);
				runCurves(r.org, r.project, r.id, known ? [known] : undefined, controller.signal)
					.then((series) => {
						const metric = known ?? mainMetric(Object.keys(series));
						if (metric && series[metric]) curves[keyOf(r)] = { metric, curve: series[metric] };
					})
					.catch(() => {});
			}
		};
		readAll();
		const id = setInterval(() => {
			if (document.visibilityState === 'visible') readAll();
		}, 30_000);
		return () => {
			clearInterval(id);
			controller.abort();
		};
	});

	const ready = $derived(runs.filter((r) => curves[keyOf(r)]));
	const pages = $derived(Math.ceil(ready.length / PER));
	let at = $state(0);
	let held = $state(false);
	$effect(() => {
		if (held || hovered || pages < 2) return;
		const id = setTimeout(() => (at = (at + 1) % pages), ROTATE * 1000);
		return () => clearTimeout(id);
	});
	// The pair on show: wrapping round so the last pair is never one run alone; a hovered row's
	// run takes the first place.
	const pair = $derived.by(() => {
		if (!ready.length) return [];
		const base = Array.from(
			{ length: Math.min(PER, ready.length) },
			(_, k) => ready[((at % pages) * PER + k) % ready.length]
		);
		const h = hovered ? ready.find((r) => keyOf(r) === hovered) : undefined;
		if (!h || base.includes(h)) return base;
		return [h, ...base.filter((r) => r !== h)].slice(0, PER);
	});
	$effect(() => {
		onshow?.(pair.map(keyOf));
	});

	const lower = (metric: string) => /loss|error|perplexity|ppl/i.test(metric);
</script>

<!-- Live runs, two side by side (Harry), the next pair every few seconds; the rows they are lit in
     Runs, and a hovered row's run comes in first. Each: whose and which on one line with its
     value, then its curve on its own range. -->
<Tile
	kind="plot"
	title="Live runs"
	bare
	{index}
	onpointerenter={() => (held = true)}
	onpointerleave={() => (held = false)}
>
	<div class="grid min-h-0 flex-1 grid-cols-2 gap-5">
		{#each pair as run (keyOf(run))}
			{@const c = curves[keyOf(run)]}
			{@const r = reading(c.curve)}
			{@const good =
				r.delta == null || r.delta === 0 ? null : lower(c.metric) ? r.delta < 0 : r.delta > 0}
			<a
				href="{runHref(run.org, run.project, run.id)}?tab=charts"
				class="flex min-w-0 flex-col"
				in:fade={{ duration: 300, delay: 150 }}
				out:fade={{ duration: 0 }}
				{@attach hint(
					`${run.owner?.id === me ? 'You' : (run.owner?.name ?? '')} · ${run.project} · ${c.metric}`
				)}
			>
				<!-- The name has the line to itself; the reading sits by the curve's end (Harry). -->
				<span class="flex min-w-0 items-center gap-2 text-[13px]">
					{#if run.owner}
						<Avatar
							name={run.owner.name}
							src={run.owner.avatar_url}
							class="size-4 flex-none text-[8px]"
						/>
					{/if}
					<span class="min-w-0 truncate">{run.name}</span>
				</span>
				<Spark curve={c.curve} draw area dot live class="mt-3 mr-1 min-h-12 flex-1">
					{#snippet end()}
						<span class="block text-[15px] font-semibold tabular-nums"
							>{formatNumber(r.last ?? NaN)}</span
						>
						{#if r.delta != null && r.delta !== 0}
							<span
								class={[
									'block text-[11px] tabular-nums',
									good ? 'text-state-finished' : 'text-muted-foreground'
								]}
								>{r.delta < 0 ? '↓' : '↑'}
								{formatNumber(Number(Math.abs(r.delta).toPrecision(2)))}</span
							>
						{/if}
					{/snippet}
				</Spark>
			</a>
		{/each}
	</div>
</Tile>
