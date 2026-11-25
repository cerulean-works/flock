<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { page as appPage } from '$app/state';
	import { SvelteSet } from 'svelte/reactivity';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { evalTable, type EvalTableRow, type EvalTableRun } from '#lib/eval-table.ts';
	import { formatScore } from '#lib/evals.ts';
	import { runHref } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	const ws = getWorkspace();
	/** Outside the org nothing can be changed, so nothing is selected (D116). */
	const readonly = $derived(appPage.data.shell?.access === 'public');

	const table = $derived.by(() => {
		void ws.tableVersion;
		// Only the columns the table reads: suite scores, the tasks' bits per byte (a suite's
		// scale), and the config `flock eval log` writes.
		const reads =
			/^(summary\.suites\/|summary\..*\/(bpb|bits_per_byte)$|config\.suite_(members|hashes)\.|config\.(dtype|attention|sdpa_backend|batch_tokens|max_length|add_bos|device|model)$)/;
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
		const add = (suite: string, depth: number) => {
			out.push({ suite, depth });
			if (open.has(suite)) for (const child of table.columns[suite].children) add(child, depth + 1);
		};
		for (const suite of table.top) add(suite, 0);
		return out;
	});
	const accuracyOver = $derived(table.top.filter((s) => table.columns[s].lower === false).length);
	const bpbOver = $derived(table.top.filter((s) => table.columns[s].lower === true).length);

	/** Runs scored under other settings than most: hidden unless asked for. */
	let showOther = $state(false);
	const others = $derived(table.rows.filter((r) => r.differs.length).length);

	// Sorted here, not by the server: a mean rank isn't a stored column. Best first on the
	// first click, the other way on the second; a run without a value goes last either way.
	let sort = $state<{ key: string; best: boolean } | null>(null);
	/** A row's number in the column sorted by: a mean rank or a comparable score. */
	const sortValue = (row: EvalTableRow, key: string) => {
		if (key === 'accuracy') return row.accuracyRank;
		if (key === 'bpb') return row.bpbRank;
		const cell = row.cells[key];
		return cell.otherHash === null ? cell.value : null;
	};
	const rows = $derived.by(() => {
		const out = table.rows.filter((r) => showOther || !r.differs.length);
		if (!sort) return out;
		const { key, best } = sort;
		if (key === 'name')
			return out.toSorted(
				(a, b) => (best ? 1 : -1) * ws.nameOf(a.row).localeCompare(ws.nameOf(b.row))
			);
		// Lower is better for ranks and bits per byte.
		const lower = key === 'accuracy' || key === 'bpb' || table.columns[key].lower === true;
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

	const scale = (lower: boolean | null) =>
		lower === null ? 'scale unknown' : lower ? 'bits per byte' : 'accuracy';
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
					<th class="px-3">
						{@render sortHeader(
							'accuracy',
							`Mean rank, accuracy (${accuracyOver})`,
							`The mean of a run's ranks over the ${accuracyOver} top accuracy suites; 1 is best. Never pooled with bits per byte.`
						)}
					</th>
					<th class="border-r border-divider px-3">
						{@render sortHeader(
							'bpb',
							`Mean rank, BPB (${bpbOver})`,
							`The mean of a run's ranks over the ${bpbOver} top bits-per-byte suites; 1 is best. Never pooled with accuracy.`
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
								{@render sortHeader(suite, suite, `${suite}: ${scale(column.lower)}`)}
							</div>
							<div class="text-right text-[11px] text-faint">{scale(column.lower)}</div>
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
						<td class="px-3 text-right">{meanRank(row.accuracyRank)}</td>
						<td class="border-r border-divider px-3 text-right">{meanRank(row.bpbRank)}</td>
						{#each shown as { suite, depth } (suite)}
							{@const cell = row.cells[suite]}
							{@const lower = table.columns[suite].lower}
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
										>{formatScore(cell.value, lower ? 'bpb' : lower === false ? 'acc' : null)}</span
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
