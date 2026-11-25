<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { page as appPage } from '$app/state';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import type { Scale } from '#lib/eval-table.ts';
	import { formatScore } from '#lib/evals.ts';
	import type { EvalsView } from '#lib/evals-view.svelte.ts';
	import { runHref } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import ColumnResizer from './ColumnResizer.svelte';

	let { view }: { view: EvalsView } = $props();

	const ws = getWorkspace();
	/** Outside the org nothing can be changed, so nothing is selected (D116). */
	const readonly = $derived(appPage.data.shell?.access === 'public');
	const table = $derived(view.table);

	// Every suite column starts as wide as the others, so the table reads evenly; a long name
	// wraps to two lines and shows whole on hover. Each edge drags, as in the runs table.
	let widths = $state.raw<Readonly<Record<string, number>>>({});
	const widthOf = (suite: string) => widths[suite] ?? 128;

	let scroller = $state<HTMLDivElement>();
	/** Scroll a suite's column (shown by then) to just past the run names. */
	export function scrollToColumn(key: string) {
		const suite = key.slice('summary.suites/'.length);
		const cell = scroller?.querySelector<HTMLElement>(`th[data-suite="${CSS.escape(suite)}"]`);
		const names = scroller?.querySelector<HTMLElement>('th[data-names]');
		if (cell && names) scroller!.scrollTo({ left: cell.offsetLeft - names.offsetWidth });
	}

	const arrow = (key: string) => (view.sort?.key === key ? (view.sort.best ? '↓' : '↑') : '');

	const SCALES = {
		accuracy: 'accuracy',
		bpb: 'bits per byte',
		loss_per_token: 'loss per token',
		perplexity: 'perplexity'
	};
	const scale = (s: Scale | null) => (s === null ? 'scale unknown' : SCALES[s]);
	const meanRank = (rank: number | null) => (rank === null ? '–' : rank.toFixed(1));
</script>

{#snippet sortHeader(key: string, label: string, help: string)}
	<button
		type="button"
		class="flex w-full items-center justify-end gap-1 font-medium whitespace-nowrap hover:text-foreground"
		onclick={() => view.sortBy(key)}
		{@attach hint(help)}
	>
		{label}<span class="w-2 text-foreground">{arrow(key)}</span>
	</button>
{/snippet}

<div class="absolute inset-0 overflow-auto pb-18" bind:this={scroller}>
	{#if !table.rows.length}
		<p class="m-4 max-w-[68ch] bg-card px-4 py-6 text-[13px] text-muted-foreground">
			No run on this page has a suite score. Eval runs logged with
			<code class="font-mono text-[0.95em] text-foreground">flock eval log</code> show here, one row each.
		</p>
	{:else}
		<table class="border-collapse text-[13px] tabular-nums">
			<thead class="sticky top-0 z-2 bg-background">
				<tr class="border-b text-xs text-muted-foreground">
					<th
						class="sticky left-0 z-1 min-w-64 bg-background px-4 text-left font-medium"
						data-names
					>
						<button
							type="button"
							class="font-medium hover:text-foreground"
							onclick={() => view.sortBy('name')}
						>
							Run <span class="text-foreground">{arrow('name')}</span>
						</button>
					</th>
					<th class="border-r border-divider px-3">
						{@render sortHeader(
							'mean',
							`Mean rank (${table.meanOver.length})`,
							`A convenience order: the mean of a run's ranks over the ${table.meanOver.length} top suites shown, accuracy and bits per byte alike; 1 is best. No result rests on it.`
						)}
					</th>
					{#each view.shown as { suite, depth } (suite)}
						{@const column = table.columns[suite]}
						{@const width = widthOf(suite)}
						<th
							class={['group/h relative px-3 py-1.5 align-bottom font-normal', depth && 'bg-card']}
							style:width="{width}px"
							style:min-width="{width}px"
							style:max-width="{width}px"
							data-suite={suite}
						>
							<div class="flex items-end justify-end gap-1">
								{#if column.children.length}
									<button
										type="button"
										class="grid size-5 flex-none place-items-center rounded hover:bg-muted"
										aria-expanded={view.open.has(suite)}
										aria-label="{view.open.has(suite) ? 'Close' : 'Open'} {suite}'s suites"
										onclick={() =>
											view.open.has(suite) ? view.open.delete(suite) : view.open.add(suite)}
									>
										<ChevronRightIcon class={['size-3.5', view.open.has(suite) && 'rotate-90']} />
									</button>
								{/if}
								<button
									type="button"
									class="line-clamp-2 min-w-0 text-right font-medium [overflow-wrap:anywhere] hover:text-foreground"
									onclick={() => view.sortBy(`suite/${suite}`)}
									{@attach hint(`${suite}: ${scale(column.scale)}`)}
								>
									{suite}
								</button>
								<span class="w-2 flex-none text-foreground">{arrow(`suite/${suite}`)}</span>
							</div>
							<div class="truncate text-right text-[11px] text-faint">{scale(column.scale)}</div>
							<ColumnResizer {width} onresize={(w) => (widths = { ...widths, [suite]: w })} />
						</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each view.rows as row (row.row)}
					{@const run = ws.page.runs[row.row]}
					<tr class="h-10 border-b border-divider hover:bg-muted">
						<th scope="row" class="sticky left-0 z-1 bg-background px-4 text-left font-normal">
							<span class="flex items-center gap-2.5">
								{#if !readonly}
									<Checkbox
										checked={ws.selected.has(run.id)}
										onCheckedChange={() => ws.toggleSelected(row.row)}
										aria-label="Select {ws.nameOf(row.row)}"
									/>
								{/if}
								<a
									class="max-w-80 truncate underline-offset-3 hover:text-link hover:underline"
									href={runHref(ws.meta.entity, ws.meta.project, run.id)}
									data-sveltekit-preload-data="tap"
									{@attach hint(ws.nameOf(row.row))}>{ws.nameOf(row.row)}</a
								>
								{#if row.instruct}
									<Badge
										variant="secondary"
										class="text-muted-foreground"
										{@attach hint(
											'Instruction-tuned: scored with the same raw prompts, no chat template'
										)}>Instruct</Badge
									>
								{/if}
								{#if row.bos}
									<Badge
										variant="secondary"
										class="text-muted-foreground"
										{@attach hint('Scored with a BOS token, as its model family asks')}>BOS</Badge
									>
								{/if}
								{#if row.device}
									<Badge variant="secondary" class="text-muted-foreground"
										>{row.device.replace(/^NVIDIA /, '')}</Badge
									>
								{/if}
								{#if row.differs.length}
									<Badge
										variant="secondary"
										class="bg-state-preempting/10 text-state-preempting"
										{@attach hint(
											`Scored with another ${row.differs.join(', ')} than most runs: not ranked`
										)}>Other settings</Badge
									>
								{/if}
							</span>
						</th>
						<td class="border-r border-divider px-3 text-right">{meanRank(row.meanRank)}</td>
						{#each view.shown as { suite, depth } (suite)}
							{@const cell = row.cells[suite]}
							{@const columnScale = table.columns[suite].scale}
							<td
								class={[
									'px-3 text-right whitespace-nowrap',
									depth && cell.heat === null && 'bg-card'
								]}
								style:background-color={cell.heat === null
									? undefined
									: `color-mix(in srgb, var(--primary) ${Math.round(4 + 32 * cell.heat)}%, var(--background))`}
							>
								{#if cell.otherHash !== null}
									<span
										class="text-faint"
										{@attach hint(
											`Scored under another suite hash (${cell.otherHash.slice(0, 12) || 'none'}), so not comparable`
										)}>–</span
									>
								{:else}
									<span {@attach hint(cell.value === null ? undefined : String(cell.value))}
										>{formatScore(
											cell.value,
											// An unknown scale reads as a plain number, never a percent.
											columnScale === 'accuracy' ? 'acc' : (columnScale ?? 'score')
										)}</span
									>
									<span class="inline-block w-5 text-left text-[11px] text-faint"
										>{cell.rank ?? ''}</span
									>
								{/if}
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>
