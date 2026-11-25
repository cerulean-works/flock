<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { page as appPage } from '$app/state';
	import { SvelteSet } from 'svelte/reactivity';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { evalTable, type EvalTableRow, type EvalTableRun, type Scale } from '#lib/eval-table.ts';
	import { formatScore } from '#lib/evals.ts';
	import { runHref } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	const ws = getWorkspace();
	/** Outside the org nothing can be changed, so nothing is selected (D116). */
	const readonly = $derived(appPage.data.shell?.access === 'public');

	const table = $derived.by(() => {
		void ws.tableVersion;
		// Only the columns the table reads: suite scores, the tasks' metrics a suite's scale is
		// read from, and the config `flock eval log` writes.
		const reads =
			/^(summary\.suites\/|summary\..*\/(bpb|bits_per_byte|loss_per_token|perplexity|acc[^/]*)$|config\.suite_(members|hashes)\.|config\.(dtype|attention|sdpa_backend|batch_tokens|add_bos|device|model)$)/;
		const read = ws.meta.columns.flatMap((c, i) =>
			reads.test(c.key) ? [[c.key, i] as const] : []
		);
		const runs: EvalTableRun[] = ws.page.runs.map((run, r) => {
			const values: Record<string, unknown> = {};
			for (const [key, i] of read) values[key] = ws.page.num[i]?.[r] ?? ws.page.text[i]?.[r];
			return { row: r, ordinal: run.ordinal, values };
		});
		return evalTable(runs);
	});

	/** Suites opened to show their member suites beside them. */
	const open = new SvelteSet<string>();
	const shown = $derived.by(() => {
		const out: { suite: string; depth: number }[] = [];
		// A suite two opened suites hold shows once, beside the first.
		const add = (suite: string, depth: number) => {
			if (out.some((c) => c.suite === suite)) return;
			out.push({ suite, depth });
			if (open.has(suite)) for (const child of table.columns[suite].children) add(child, depth + 1);
		};
		for (const suite of table.top) add(suite, 0);
		return out;
	});
	const counted = $derived(table.meanOver.length);

	/** Runs scored under other settings than most: hidden unless asked for. */
	let showOther = $state(false);
	const others = $derived(table.rows.filter((r) => r.differs.length).length);

	// Sorted here, not by the server: a mean rank isn't a stored column. Best first on the
	// first click, the other way on the second; a run without a value goes last either way.
	// A suite's key is `suite/<name>`, apart from the run's name and the mean rank.
	let sort = $state<{ key: string; best: boolean } | null>(null);
	/** A row's number in the column sorted by: a mean rank or a ranked score. */
	const sortValue = (row: EvalTableRow, key: string) => {
		if (key === 'mean') return row.meanRank;
		// Only ranked scores order a column; the rest follow, as runs without a value do.
		const cell = row.cells[key.slice('suite/'.length)];
		return cell.rank === null ? null : cell.value;
	};
	const rows = $derived.by(() => {
		const out = table.rows.filter((r) => showOther || !r.differs.length);
		const column = sort?.key.startsWith('suite/')
			? table.columns[sort.key.slice('suite/'.length)]
			: undefined;
		// A suite sorted by on an earlier page that this page has no score for: page order.
		if (!sort || (sort.key.startsWith('suite/') && !column)) return out;
		const { key, best } = sort;
		if (key === 'name')
			return out.toSorted(
				(a, b) => (best ? 1 : -1) * ws.nameOf(a.row).localeCompare(ws.nameOf(b.row))
			);
		// Lower is better for ranks, and on every scale but accuracy.
		const lower = !column || column.scale !== 'accuracy';
		const sign = lower === best ? 1 : -1;
		return out.toSorted((a, b) => {
			const [x, y] = [sortValue(a, key), sortValue(b, key)];
			if (x === null || y === null) return Number(x === null) - Number(y === null);
			return sign * (x - y);
		});
	});
	const sortBy = (key: string) =>
		(sort = sort?.key === key ? { key, best: !sort.best } : { key, best: true });
	const arrow = (key: string) => (sort?.key === key ? (sort.best ? '↓' : '↑') : '');

	const SCALES = {
		accuracy: 'accuracy',
		bpb: 'bits per byte',
		loss_per_token: 'loss per token',
		perplexity: 'perplexity'
	};
	const scale = (s: Scale | null) => (s === null ? 'scale unknown' : SCALES[s]);
	const meanRank = (rank: number | null) => (rank === null ? '–' : rank.toFixed(1));
	const narrowed = $derived(ws.page.total > ws.page.runs.length);
</script>

{#snippet sortHeader(key: string, label: string, help: string)}
	<button
		type="button"
		class="flex w-full items-center justify-end gap-1 font-medium whitespace-nowrap hover:text-foreground"
		onclick={() => sortBy(key)}
		{@attach hint(help)}
	>
		{label}<span class="w-2 text-foreground">{arrow(key)}</span>
	</button>
{/snippet}

<div class="absolute inset-0 overflow-auto pb-18">
	{#if !table.rows.length}
		<p class="m-4 max-w-[68ch] bg-card px-4 py-6 text-[13px] text-muted-foreground">
			No run on this page has a suite score. Eval runs logged with
			<code class="font-mono text-[0.95em] text-foreground">flock eval log</code> show here, one row each.
		</p>
	{:else}
		<div class="flex min-h-10 items-center gap-3 px-4 text-[13px] text-muted-foreground">
			<span>
				Ranked among the {table.rows.length - others} runs{narrowed ? ' on this page' : ''} scored under
				the same settings. Darker is better within a column.
			</span>
			{#if others}
				<Button variant="outline" size="sm" onclick={() => (showOther = !showOther)}>
					{showOther ? 'Hide' : 'Show'}
					{others} on other settings
				</Button>
			{/if}
		</div>
		<table class="border-collapse text-[13px] tabular-nums">
			<thead class="sticky top-0 z-2 bg-background">
				<tr class="h-12 border-b text-xs text-muted-foreground">
					<th class="sticky left-0 z-1 min-w-64 bg-background px-4 text-left font-medium">
						<button
							type="button"
							class="font-medium hover:text-foreground"
							onclick={() => sortBy('name')}
						>
							Run <span class="text-foreground">{arrow('name')}</span>
						</button>
					</th>
					<th class="border-r border-divider px-3">
						{@render sortHeader(
							'mean',
							`Mean rank (${counted})`,
							`A convenience order: the mean of a run's ranks over the ${counted} top suites, accuracy and bits per byte alike; 1 is best. No result rests on it.`
						)}
					</th>
					{#each shown as { suite, depth } (suite)}
						{@const column = table.columns[suite]}
						<th class={['px-3 font-normal', depth && 'bg-card']}>
							<div class="flex items-center justify-end gap-1">
								{#if column.children.length}
									<button
										type="button"
										class="grid size-5 place-items-center rounded hover:bg-muted"
										aria-expanded={open.has(suite)}
										aria-label="{open.has(suite) ? 'Close' : 'Open'} {suite}'s suites"
										onclick={() => (open.has(suite) ? open.delete(suite) : open.add(suite))}
									>
										<ChevronRightIcon class={['size-3.5', open.has(suite) && 'rotate-90']} />
									</button>
								{/if}
								{@render sortHeader(`suite/${suite}`, suite, `${suite}: ${scale(column.scale)}`)}
							</div>
							<div class="text-right text-[11px] text-faint">{scale(column.scale)}</div>
						</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each rows as row (row.row)}
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
						{#each shown as { suite, depth } (suite)}
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
