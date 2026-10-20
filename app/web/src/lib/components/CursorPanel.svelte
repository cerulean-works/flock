<script lang="ts">
	import { formatX } from '#lib/chart/axes.ts';
	import { pointAt, smoothed } from '#lib/chart/series.ts';
	import { formatMetric } from '#lib/utils.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	/**
	 * Floating readout for the panel under the pointer: every plotted line's value at the
	 * crosshair step, sorted high to low, windowed around the line nearest the pointer. A line
	 * is a run's, named by its metric too on panels of several metrics.
	 */

	const ws = getWorkspace();
	const MAX_ROWS = 14;
	const WIDTH = 320;

	// Nothing to read out while a panel or section is dragged over the plots.
	const cursor = $derived(ws.dragging ? null : ws.cursor);
	const view = $derived(cursor?.view ?? null);

	interface Row {
		row: number;
		metric: number;
		color: string;
		value: number;
		raw: number;
		/** Where the line's point is: the cursor's step, unless the line logged none there. */
		step: number;
	}

	const readout = $derived.by(() => {
		const c = cursor;
		if (!c) return null;
		const v = c.view;
		void ws.version(v);
		const axis = ws.axisOf(v);
		const smoothing = v.config.smoothing ?? ws.smoothing;
		const rows: Row[] = [];
		// The plot snapped the cursor to a logged step; each line reads out at its point there
		// (its nearest, for lines logged at other steps).
		for (const row of ws.visible) {
			v.metrics.forEach((metric, k) => {
				const s = ws.lineSeries(row, metric, axis);
				if (!s) return;
				const i = pointAt(s, c.step, c.tol);
				if (i < 0) return;
				const value = smoothed(s, smoothing)[i];
				const color = ws.lineColor(v, row, k);
				if (!Number.isNaN(value))
					rows.push({ row, metric, color, value, raw: s.y[i], step: s.stepAt(i) });
			});
		}
		rows.sort((a, b) => b.value - a.value);

		const focus = ws.focus;
		const at =
			focus == null
				? -1
				: rows.findIndex((r) => r.row === focus && (c.line == null || r.metric === c.line));
		const start =
			at < 0 ? 0 : Math.max(0, Math.min(at - Math.floor(MAX_ROWS / 2), rows.length - MAX_ROWS));
		return {
			step: c.step,
			axis,
			smoothing,
			total: rows.length,
			above: start,
			below: Math.max(0, rows.length - start - MAX_ROWS),
			rows: rows.slice(start, start + MAX_ROWS)
		};
	});

	/** The line under the pointer, or every line of the focused run when it isn't on one. */
	const isFocused = (r: Row) =>
		r.row === ws.focus &&
		(cursor?.line == null || cursor.nearest !== r.row || r.metric === cursor.line);

	let height = $state(0);
	const left = $derived(
		!cursor
			? 0
			: cursor.clientX + 20 + WIDTH > innerWidth
				? cursor.clientX - 20 - WIDTH
				: cursor.clientX + 20
	);
	const top = $derived(
		!cursor ? 0 : Math.max(8, Math.min(cursor.clientY - 24, innerHeight - height - 8))
	);
</script>

{#if cursor && readout && view && readout.total}
	{@const several = view.metrics.length > 1}
	{@const byMetric = ws.byMetric(view)}
	<!-- Above dialogs too: the panel editor previews its plot under the pointer. -->
	<div
		class="cursor-panel pointer-events-none fixed top-0 left-0 z-60 rounded-lg bg-popover p-1 text-xs text-popover-foreground tabular-nums shadow-md ring-1 ring-foreground/10 will-change-transform"
		style:width="{WIDTH}px"
		style:transform="translate({left}px, {top}px)"
		bind:clientHeight={height}
		aria-hidden="true"
	>
		<div
			class="mb-1 flex justify-between gap-3 border-b border-divider px-2 pt-1 pb-1.5 text-muted-foreground"
		>
			<span class="truncate font-medium text-foreground">{view.title}</span>
			<span class="flex-none">{formatX(readout.axis, readout.step)}</span>
		</div>
		{#if readout.above}
			<div class="px-2 py-0.5 text-faint">{readout.above} more above</div>
		{/if}
		{#each readout.rows as r (`${r.row}:${r.metric}`)}
			{@const focused = isFocused(r)}
			{@const label = ws.meta.metrics[r.metric].label}
			<div
				class={[
					'flex h-[22px] items-center gap-2 rounded-md px-2',
					focused ? 'bg-accent text-foreground' : 'text-muted-foreground'
				]}
			>
				<span
					class={['w-3 flex-none rounded-full', focused ? 'h-[3px]' : 'h-0.5']}
					style:background={r.color}
				></span>
				<span class="min-w-[58px] flex-none font-semibold text-foreground"
					>{formatMetric(r.value, view.format)}</span
				>
				{#if readout.smoothing > 0}<span class="flex-none text-faint"
						>{formatMetric(r.raw, view.format)}</span
					>{/if}
				{#if byMetric}
					<span class="truncate">{label}</span>
				{:else}
					<span class="truncate"
						>{ws.page.runs[r.row].name}{#if several}<span class="text-faint">
								· {label}</span
							>{/if}</span
					>
				{/if}
				{#if r.step !== readout.step}
					<!-- Logged at another step than the crosshair's: say which. -->
					<span class="ml-auto flex-none text-faint">{formatX(readout.axis, r.step)}</span>
				{/if}
			</div>
		{/each}
		{#if readout.below}
			<div class="px-2 py-0.5 text-faint">{readout.below} more below</div>
		{/if}
	</div>
{/if}
