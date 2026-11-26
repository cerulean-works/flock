<script lang="ts" module>
	/** A line of Compare's history chart: one group's best run. */
	export interface CompareLine {
		run: string;
		/** What the legend names it: the group. */
		label: string;
		color: string;
		/** The run's own final value of the metric (its summary), shown at the line's end. */
		value: number | null;
	}
</script>

<script lang="ts">
	import LinePlot from '#lib/components/LinePlot.svelte';
	import { distinctLabels } from '#lib/groups.ts';
	import { formatNumber } from '#lib/utils.ts';
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

	/** Each line's last step (`summary._step`): runs trained for different lengths end apart. */
	const stepOf = $derived.by(() => {
		const col = data.meta.columns.findIndex((c) => c.key === 'summary._step');
		const nums = col === -1 ? null : data.page.num[col];
		return new Map(
			data.page.runs.map((r, row) => {
				const step = nums?.[row];
				return [r.id, step != null && Number.isFinite(step) ? step : null];
			})
		);
	});
	/** Legend labels that keep each group's difference in view (`distinctLabels`). */
	const shortLabels = $derived(distinctLabels(lines.map((l) => l.label)));
	const steps = $derived(lines.map((l) => stepOf.get(l.run) ?? null));
	const uneven = $derived(new Set(steps.filter((s) => s != null)).size > 1);
	const fmtStep = (step: number) =>
		step >= 1000 ? `${formatNumber(step / 1000)}k` : String(Math.round(step));
</script>

<div class="flex min-h-0 flex-1 flex-col gap-3" bind:this={root}>
	<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
		{#each lines as l, i (l.run)}
			<li
				class={[
					'flex min-w-0 items-center gap-2 transition-opacity',
					hovered != null && hovered !== l.run && 'opacity-45'
				]}
				onpointerenter={() => onhover(l.run)}
				onpointerleave={() => onhover(null)}
			>
				<span class="h-0.5 w-3.5 flex-none rounded-full" style:background={l.color}></span>
				<span class="max-w-56 truncate" title={l.label}>{shortLabels[i]}</span>
				{#if l.value != null}
					<span class="text-foreground tabular-nums">{formatNumber(l.value)}</span>
				{/if}
				{#if uneven && stepOf.get(l.run) != null}
					<span class="text-faint tabular-nums">@ step {fmtStep(stepOf.get(l.run)!)}</span>
				{/if}
			</li>
		{/each}
	</ul>
	{#if uneven}
		<p class="-mt-1 text-xs text-muted-foreground">
			These runs trained for different numbers of steps, so a curve that sits lower can still end
			worse. The values above are where each run ended, which is what the table ranks.
		</p>
	{/if}
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
