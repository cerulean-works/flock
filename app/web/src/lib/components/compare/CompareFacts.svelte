<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ArrowUpDownIcon from '@lucide/svelte/icons/arrow-up-down';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { byDifference, pairRows, sameValue, type PairRow } from '#lib/compare.ts';
	import type { RunDetail } from '#lib/types.ts';
	import { dateTime, duration, formatNumber, formatValue, gpus, hours } from '#lib/utils.ts';

	/**
	 * Two runs' facts side by side (the compare page's Summary, Config and System): one flat table,
	 * what differs in full ink and what agrees muted, so the eye lands on the differences.
	 */
	interface Props {
		view: 'summary' | 'config' | 'system';
		a: RunDetail;
		b: RunDetail;
		/** Keys narrowed to those containing it (the view bar's search). */
		query?: string;
	}

	let { view, a, b, query = '' }: Props = $props();

	const matches = (key: string) => {
		const q = query.trim().toLowerCase();
		return !q || key.toLowerCase().includes(q);
	};

	// Summary: most different first until a header is clicked; wandb's bookkeeping left out.
	let order = $state<'difference' | 'key'>('difference');
	const summary = $derived.by(() => {
		const rows = pairRows(a.summary, b.summary, (k) => k.startsWith('_')).filter((r) =>
			matches(r.key)
		);
		return order === 'difference' ? byDifference(rows) : rows;
	});

	// Config: the keys that differ, then the ones that agree, folded.
	const config = $derived(pairRows(a.config, b.config).filter((r) => matches(r.key)));
	const differs = $derived(config.filter((r) => !r.same));
	const agrees = $derived(config.filter((r) => r.same));
	let showSame = $state(false);

	// System: the run's own facts, in the run page's order.
	const facts = (r: RunDetail): [string, unknown][] => [
		['Project', `${r.entity}/${r.project}`],
		['State', r.state],
		['Job type', r.job_type],
		['Created', dateTime(r.created_at)],
		['Runtime', r.runtime ? duration(r.runtime) : null],
		['GPUs', gpus(r.gpu_type, r.gpu_count)],
		['GPU hours', r.gpu_hours ? hours(r.gpu_hours) : null],
		['Host', r.host],
		['Program', r.program],
		['Commit', r.commit],
		['Group', r.group],
		['Tags', r.tags.length ? r.tags.join(', ') : null],
		['Owner', r.owner_user?.name ?? null]
	];
	const system = $derived.by((): PairRow[] => {
		const right = new Map(facts(b));
		return facts(a).map(([key, x]) => {
			const y = right.get(key);
			const same = sameValue(x ?? null, y ?? null);
			return { key, a: x, b: y, same, delta: null, spread: same ? 0 : 1 };
		});
	});

	const numeric = (r: PairRow) => typeof r.a === 'number' || typeof r.b === 'number';

	/** Full precision on hover, where the cell rounds or cuts. */
	const exact = (v: unknown) =>
		v === undefined ? 'Not logged' : typeof v === 'number' ? String(v) : formatValue(v);
	const shown = (v: unknown) =>
		v === undefined ? '—' : typeof v === 'number' ? formatNumber(v) : formatValue(v);
	const signed = (d: number) =>
		d === 0 ? '0' : `${d > 0 ? '+' : '−'}${formatNumber(Math.abs(d))}`;
</script>

<!-- A row with a number on either side sets both right, so a missing side's dash lines up. -->
{#snippet value(v: unknown, same: boolean, mono = false, right = typeof v === 'number')}
	<td
		class={[
			'truncate px-3 py-1.5',
			right && 'text-right tabular-nums',
			mono && 'font-mono text-[12.5px]',
			same || v === undefined || v === null ? 'text-muted-foreground' : 'text-foreground'
		]}
		{@attach hint(exact(v))}
	>
		{shown(v)}
	</td>
{/snippet}

{#snippet head(columns: { label: string; right?: boolean }[])}
	<thead class="border-b border-divider text-xs text-muted-foreground">
		<tr>
			{#each columns as c (c.label)}
				<th
					class={['px-3 py-2 font-medium whitespace-nowrap', c.right ? 'text-right' : 'text-left']}
					>{c.label}</th
				>
			{/each}
		</tr>
	</thead>
{/snippet}

{#snippet empty(text: string)}
	<p class="py-6 text-center text-[13px] text-muted-foreground">{text}</p>
{/snippet}

<!-- The house table: flat, hairline rows of 36px, the text on the page's 20px edges. -->
<div class="bleed overflow-x-auto">
	{#if view === 'summary'}
		{#if summary.length}
			<table
				class="w-full min-w-[40rem] table-fixed text-[13px] [&_tr>*:first-child]:pl-5 [&_tr>*:last-child]:pr-5"
			>
				<colgroup>
					<col />
					<col class="w-[11rem]" />
					<col class="w-[11rem]" />
					<col class="w-[9rem]" />
				</colgroup>
				<thead class="border-b border-divider text-xs text-muted-foreground">
					<tr>
						<th class="px-3 py-2 text-left font-medium">
							<button
								type="button"
								class="group/sort inline-flex items-center gap-1 hover:text-foreground"
								onclick={() => (order = 'key')}
								aria-pressed={order === 'key'}
							>
								Key
								<ArrowUpDownIcon
									class={[
										'size-3',
										order === 'key' ? 'text-foreground' : 'opacity-0 group-hover/sort:opacity-50'
									]}
								/>
							</button>
						</th>
						<th class="px-3 py-2 text-right font-medium">A</th>
						<th class="px-3 py-2 text-right font-medium">B</th>
						<th class="px-3 py-2 text-right font-medium">
							<button
								type="button"
								class="group/sort inline-flex items-center gap-1 hover:text-foreground"
								onclick={() => (order = 'difference')}
								aria-pressed={order === 'difference'}
								{@attach hint('Most different first')}
							>
								A − B
								<ArrowDownIcon
									class={[
										'size-3',
										order === 'difference'
											? 'text-foreground'
											: 'opacity-0 group-hover/sort:opacity-50'
									]}
								/>
							</button>
						</th>
					</tr>
				</thead>
				<tbody>
					{#each summary as r (r.key)}
						<tr class="h-9 border-b border-divider">
							<td
								class={['truncate px-3 py-1.5', r.same && 'text-muted-foreground']}
								{@attach hint(r.key)}>{r.key}</td
							>
							{@render value(r.a, r.same, false, numeric(r))}
							{@render value(r.b, r.same, false, numeric(r))}
							<td
								class={[
									'px-3 py-1.5 text-right tabular-nums',
									r.delta ? 'text-foreground' : 'text-muted-foreground'
								]}
								{@attach hint(r.delta != null ? String(r.delta) : undefined)}
							>
								{r.delta != null ? signed(r.delta) : r.same ? '' : '—'}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			{@render empty(query.trim() ? 'No summary keys match.' : 'Neither run has a summary.')}
		{/if}
	{:else if view === 'config'}
		{#if config.length}
			<table
				class="w-full min-w-[40rem] table-fixed text-[13px] [&_tr>*:first-child]:pl-5 [&_tr>*:last-child]:pr-5"
			>
				<colgroup>
					<col class="w-[40%]" />
					<col />
					<col />
				</colgroup>
				{@render head([{ label: 'Key' }, { label: 'A' }, { label: 'B' }])}
				<tbody>
					{#each differs as r (r.key)}
						<tr class="h-9 border-b border-divider">
							<td class="truncate px-3 py-1.5" {@attach hint(r.key)}>{r.key}</td>
							{@render value(r.a, false, false, numeric(r))}
							{@render value(r.b, false, false, numeric(r))}
						</tr>
					{:else}
						<tr class="h-9 border-b border-divider">
							<td colspan="3" class="px-3 py-1.5 text-muted-foreground">No differences</td>
						</tr>
					{/each}
					{#if agrees.length}
						<!-- The keys that agree, folded: one value each, across both columns. -->
						<tr class="h-9 border-b border-divider">
							<td colspan="3" class="px-3 py-1.5">
								<button
									type="button"
									class="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
									aria-expanded={showSame}
									onclick={() => (showSame = !showSame)}
								>
									<ChevronRightIcon
										class={['size-3.5 transition-transform', showSame && 'rotate-90']}
									/>
									{agrees.length} the same
								</button>
							</td>
						</tr>
						{#if showSame}
							{#each agrees as r (r.key)}
								<tr class="h-9 border-b border-divider text-muted-foreground">
									<td class="truncate px-3 py-1.5" {@attach hint(r.key)}>{r.key}</td>
									<td colspan="2" class="truncate px-3 py-1.5" {@attach hint(exact(r.a))}
										>{shown(r.a)}</td
									>
								</tr>
							{/each}
						{/if}
					{/if}
				</tbody>
			</table>
		{:else}
			{@render empty(query.trim() ? 'No config keys match.' : 'Neither run has a config.')}
		{/if}
	{:else}
		<table
			class="w-full min-w-[40rem] table-fixed text-[13px] [&_tr>*:first-child]:pl-5 [&_tr>*:last-child]:pr-5"
		>
			<colgroup>
				<col class="w-[12rem]" />
				<col />
				<col />
			</colgroup>
			{@render head([{ label: '' }, { label: 'A' }, { label: 'B' }])}
			<tbody>
				{#each system as r (r.key)}
					<tr class="h-9 border-b border-divider">
						<td class="px-3 py-1.5 text-muted-foreground">{r.key}</td>
						{@render value(r.a ?? undefined, r.same, r.key === 'Commit')}
						{@render value(r.b ?? undefined, r.same, r.key === 'Commit')}
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>
