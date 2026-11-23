<script lang="ts" module>
	/** A line of Compare's history chart: one group's best run. */
	export interface CompareLine {
		run: string;
		/** What the legend names it: the group. */
		label: string;
		color: string;
	}
</script>

<script lang="ts">
	import LinePlot from '#lib/components/LinePlot.svelte';
	import type { WorkspaceData } from '#lib/types.ts';
	import { Workspace, setWorkspace } from '#lib/workspace/workspace.svelte.ts';

	interface Props {
		/** The lines' runs as the workspace snapshot has them (their histories). */
		data: WorkspaceData;
		lines: CompareLine[];
		/** The history key charted: the best-by metric. */
		metric: string;
		/** The run hovered here or in the table. */
		hovered: string | null;
		onhover: (run: string | null) => void;
	}

	let { data, lines, metric, hovered, onhover }: Props = $props();

	/**
	 * The charts' engine and `LinePlot` as the Runs page has them, over a page of only these
	 * runs, each in its group's color (D188).
	 */
	const ws = new Workspace(
		() => data.meta,
		() => data.page,
		() => [],
		{
			style: () => {
				const color = new Map(lines.map((l) => [l.run, l.color]));
				return {
					colors: data.page.runs.map((r) => color.get(r.id) ?? '#8a919c'),
					means: [],
					band: 'std',
					legend: [],
					only: new Set(
						data.page.runs.flatMap((r, row) => (color.has(r.id) ? [row] : []))
					) as ReadonlySet<number>
				};
			}
		}
	);
	setWorkspace(ws);

	const panel = $derived(ws.findPanel(metric));
	const rowOf = $derived(new Map(data.page.runs.map((r, row) => [r.id, row])));

	// Hovering a row of the table brings its line forward, and a line its row.
	$effect(() => {
		const row = hovered == null ? undefined : rowOf.get(hovered);
		ws.hover = row ?? null;
	});
	$effect(() => {
		const row = ws.hover;
		onhover(row == null ? null : (data.page.runs[row]?.id ?? null));
	});

	let root = $state<HTMLElement | null>(null);
</script>

<div class="flex min-h-0 flex-1 flex-col gap-3" bind:this={root}>
	<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
		{#each lines as l (l.run)}
			<li
				class={[
					'flex min-w-0 items-center gap-2 transition-opacity',
					hovered != null && hovered !== l.run && 'opacity-45'
				]}
				onpointerenter={() => onhover(l.run)}
				onpointerleave={() => onhover(null)}
			>
				<span class="h-0.5 w-3.5 flex-none rounded-full" style:background={l.color}></span>
				<span class="max-w-56 truncate" title={l.label}>{l.label}</span>
			</li>
		{/each}
	</ul>
	<div class="min-h-0 flex-1">
		{#if panel === -1}
			<div class="grid h-full place-items-center text-[13px] text-faint">
				{metric} was logged as a summary only: it has no history to chart
			</div>
		{:else}
			<LinePlot {panel} surface="fullscreen" active={true} {root} />
		{/if}
	</div>
</div>
