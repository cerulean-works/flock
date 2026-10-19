<script lang="ts">
	import { getWorkspace, metricColor, type PanelView } from '#lib/workspace/workspace.svelte.ts';

	interface Props {
		view: PanelView;
		/** Only when it names the panel's metrics (the full-screen panel lists runs beside it). */
		metricsOnly?: boolean;
		class?: string;
	}

	let { view, metricsOnly = false, class: className }: Props = $props();

	const ws = getWorkspace();

	/** Most lines a legend names; more are summed up. */
	const MAX = 12;

	/**
	 * The panel's metrics when its lines are colored by metric (one run), else the runs plotted.
	 * Unless the panel says otherwise, it shows when it names few lines: up to five runs (a
	 * run's own page has just the one), or eight metrics.
	 */
	const legend = $derived.by(() => {
		const show = view.config.legend;
		const metrics = ws.byMetric(view);
		if (show === false || (metricsOnly && !metrics)) return null;
		const entries = metrics
			? view.metrics.map((m, k) => ({
					key: `metric:${m}`,
					color: metricColor(k),
					label: ws.meta.metrics[m].label,
					row: null
				}))
			: ws.visible.map((row) => ({
					key: ws.page.runs[row].id,
					color: ws.colors[row],
					label: ws.page.runs[row].name,
					row
				}));
		const auto = metrics
			? entries.length <= 8
			: entries.length <= 5 && ws.page.runs.length > 1 && view.metrics.length === 1;
		if (!entries.length || (show !== true && !auto)) return null;
		return { metrics, entries: entries.slice(0, MAX), more: entries.length - MAX };
	});
</script>

{#if legend}
	<!-- Metrics wrap in columns (a GPU chart names eight); runs are listed. -->
	<ul
		class={[
			'pl-1.5 text-xs',
			legend.metrics
				? 'grid grid-cols-[repeat(auto-fill,minmax(9.5rem,1fr))] gap-x-3 gap-y-0.5'
				: 'flex flex-col gap-0.5',
			className
		]}
	>
		{#each legend.entries as entry (entry.key)}
			{@const row = entry.row}
			<li
				class={[
					'flex min-w-0 items-center gap-2 transition-opacity',
					row != null && ws.focus != null && ws.focus !== row && 'opacity-45'
				]}
				onpointerenter={() => row != null && (ws.hover = row)}
				onpointerleave={() => row != null && (ws.hover = null)}
			>
				<span class="h-0.5 w-3.5 flex-none rounded-full" style:background={entry.color}></span>
				<span class="truncate" title={entry.label}>{entry.label}</span>
			</li>
		{/each}
		{#if legend.more > 0}
			<li class="text-faint">{legend.more} more</li>
		{/if}
	</ul>
{/if}
