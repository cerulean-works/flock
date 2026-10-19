<script lang="ts">
	import { slotOf, smoothed } from '#lib/chart/series.ts';
	import { duration, formatMetric, formatStep } from '#lib/utils.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	/**
	 * Floating readout for the panel under the pointer: every plotted run's value at the
	 * crosshair step, sorted high to low, windowed around the run nearest the pointer.
	 */

	const ws = getWorkspace();
	const MAX_ROWS = 14;
	const WIDTH = 320;

	const cursor = $derived(ws.cursor);

	interface Row {
		row: number;
		value: number;
		raw: number;
	}

	const readout = $derived.by(() => {
		const c = cursor;
		if (!c) return null;
		void ws.version(c.metric);
		const page = ws.page;
		const smoothing = ws.smoothing;
		const rows: Row[] = [];
		let step = c.step;
		for (const row of ws.visible) {
			const s = page.series[slotOf(page.metricCount, row, c.metric)];
			if (!s || !s.covers(c.step)) continue;
			const i = s.indexNear(c.step);
			if (row === c.nearest) step = s.stepAt(i);
			const value = smoothed(s, smoothing)[i];
			if (!Number.isNaN(value)) rows.push({ row, value, raw: s.y[i] });
		}
		rows.sort((a, b) => b.value - a.value);

		const focus = ws.focus;
		const at = focus == null ? -1 : rows.findIndex((r) => r.row === focus);
		const start =
			at < 0 ? 0 : Math.max(0, Math.min(at - Math.floor(MAX_ROWS / 2), rows.length - MAX_ROWS));
		return {
			step,
			total: rows.length,
			above: start,
			below: Math.max(0, rows.length - start - MAX_ROWS),
			rows: rows.slice(start, start + MAX_ROWS)
		};
	});

	const def = $derived(cursor ? ws.meta.metrics[cursor.metric] : null);

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

{#if cursor && readout && def && readout.total}
	<div
		class="cursor-panel pointer-events-none fixed top-0 left-0 z-50 rounded-lg bg-popover p-1 text-xs text-popover-foreground tabular-nums shadow-md ring-1 ring-foreground/10 will-change-transform"
		style:width="{WIDTH}px"
		style:transform="translate({left}px, {top}px)"
		bind:clientHeight={height}
		aria-hidden="true"
	>
		<div
			class="mb-1 flex justify-between gap-3 border-b border-divider px-2 pt-1 pb-1.5 text-muted-foreground"
		>
			<span class="truncate font-medium text-foreground">{def.key}</span>
			<span class="flex-none"
				>{def.stream === 'system'
					? `Runtime ${duration(readout.step)}`
					: `Step ${formatStep(readout.step)}`}</span
			>
		</div>
		{#if readout.above}
			<div class="px-2 py-0.5 text-faint">{readout.above} more above</div>
		{/if}
		{#each readout.rows as r (r.row)}
			{@const focused = r.row === ws.focus}
			<div
				class={[
					'flex h-[22px] items-center gap-2 rounded-md px-2',
					focused ? 'bg-accent text-foreground' : 'text-muted-foreground'
				]}
			>
				<span
					class={['w-3 flex-none rounded-full', focused ? 'h-[3px]' : 'h-0.5']}
					style:background={ws.colors[r.row]}
				></span>
				<span class="min-w-[58px] flex-none font-semibold text-foreground"
					>{formatMetric(r.value, def.format)}</span
				>
				{#if ws.smoothing > 0}<span class="flex-none text-faint"
						>{formatMetric(r.raw, def.format)}</span
					>{/if}
				<span class="truncate">{ws.page.runs[r.row].name}</span>
			</div>
		{/each}
		{#if readout.below}
			<div class="px-2 py-0.5 text-faint">{readout.below} more below</div>
		{/if}
	</div>
{/if}
