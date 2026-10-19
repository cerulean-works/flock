<script lang="ts">
	import SearchIcon from '@lucide/svelte/icons/search';
	import Avatar from '#lib/components/Avatar.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { flatten } from '#lib/data/run.ts';
	import type { RunDetail } from '#lib/types.ts';
	import { dateTime, duration, formatValue, gpus, hours, money, relativeTime } from '#lib/utils.ts';

	interface Props {
		run: RunDetail;
		/** Its state, as the live stream reports it. */
		runState: string;
		owner: { name: string; avatar_url: string | null } | null;
		currency: string | null;
		now: number;
	}

	let { run, runState, owner, currency, now }: Props = $props();

	let query = $state('');
	const config = $derived(flatten(run.config).sort(([a], [b]) => a.localeCompare(b)));
	// wandb's bookkeeping (`_step`, `_runtime`, ...) is in the details already.
	const summary = $derived(
		flatten(run.summary)
			.filter(([k]) => !k.startsWith('_'))
			.sort(([a], [b]) => a.localeCompare(b))
	);
	const match = (rows: [string, unknown][]) => {
		const q = query.trim().toLowerCase();
		return q ? rows.filter(([k]) => k.toLowerCase().includes(q)) : rows;
	};
	const shownConfig = $derived(match(config));
	const shownSummary = $derived(match(summary));

	/** Full precision on hover, where the table rounds. */
	const exact = (v: unknown) =>
		typeof v === 'number' ? String(v) : typeof v === 'string' ? v : undefined;

	const gpu = $derived(gpus(run.gpu_type, run.gpu_count));
</script>

{#snippet field(label: string, value: string | null | undefined, mono = false)}
	<div class="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-3 py-1.5">
		<dt class="text-muted-foreground">{label}</dt>
		<dd class={['min-w-0 break-words', mono && 'font-mono text-[12.5px]', !value && 'text-faint']}>
			{value || '–'}
		</dd>
	</div>
{/snippet}

{#snippet table(title: string, rows: [string, unknown][], total: number)}
	<section class="min-w-0">
		<h2 class="mb-2 flex items-baseline gap-2 font-serif text-[22px] tracking-[-0.01em]">
			{title}
			<span class="font-sans text-[13px] text-muted-foreground tabular-nums">
				{rows.length === total ? total : `${rows.length} of ${total}`}
			</span>
		</h2>
		<div class="overflow-hidden rounded-xl ring-1 ring-foreground/10">
			<table class="w-full table-fixed text-[13px]">
				<tbody>
					{#each rows as [key, value] (key)}
						<tr class="border-b border-divider last:border-0 hover:bg-muted">
							<th
								class="w-[45%] truncate px-3 py-2 text-left font-mono text-[12.5px] font-normal"
								title={key}
							>
								{key}
							</th>
							<td
								class={[
									'truncate px-3 py-2 tabular-nums',
									typeof value === 'number' && 'text-right',
									formatValue(value) === '–' && 'text-faint'
								]}
								title={exact(value)}
							>
								{formatValue(value)}
							</td>
						</tr>
					{:else}
						<tr>
							<td class="px-3 py-3 text-muted-foreground">
								{total ? 'No matching keys' : 'Nothing logged'}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{/snippet}

<div class="mx-auto grid max-w-280 gap-10 px-5 pt-6 pb-16">
	<section class="grid gap-x-12 lg:grid-cols-2">
		<dl class="text-[13.5px]">
			<div class="grid grid-cols-[8.5rem_minmax(0,1fr)] items-center gap-3 py-1.5">
				<dt class="text-muted-foreground">State</dt>
				<dd><StateBadge state={runState} /></dd>
			</div>
			<div class="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-3 py-1.5">
				<dt class="text-muted-foreground">Started</dt>
				<dd title={dateTime(run.created_at)}>
					{dateTime(run.created_at)}
					<span class="text-muted-foreground">· {relativeTime(run.created_at, now)}</span>
				</dd>
			</div>
			{@render field('Runtime', duration(run.runtime))}
			<div class="grid grid-cols-[8.5rem_minmax(0,1fr)] items-center gap-3 py-1.5">
				<dt class="text-muted-foreground">Logged by</dt>
				<dd class="flex min-w-0 items-center gap-2">
					{#if owner}
						<Avatar name={owner.name} src={owner.avatar_url} class="size-5 text-[9px]" />
						<span class="truncate">{owner.name}</span>
					{:else}
						<span class="text-faint">–</span>
					{/if}
				</dd>
			</div>
			{@render field('Host', run.host)}
			{@render field('Command', run.program, true)}
			{@render field('Commit', run.commit, true)}
		</dl>
		<dl class="text-[13.5px]">
			{@render field('Group', run.group)}
			{@render field('Job type', run.job_type)}
			{@render field('Sweep', run.sweep, true)}
			{@render field('GPUs', gpu)}
			{@render field(
				'GPU hours',
				run.gpu_hours
					? `${hours(run.gpu_hours)}${currency ? ` · ${money(run.cost, currency)}` : ''}`
					: null
			)}
			{@render field('Exit code', run.exitcode == null ? null : String(run.exitcode))}
			<div class="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-3 py-1.5">
				<dt class="text-muted-foreground">Tags</dt>
				<dd class="flex min-w-0 flex-wrap gap-1">
					{#each run.tags as tag (tag)}
						<Badge variant="secondary">{tag}</Badge>
					{:else}
						<span class="text-faint">–</span>
					{/each}
				</dd>
			</div>
		</dl>
		{#if run.notes || run.description}
			<div class="mt-4 grid gap-2 border-t border-divider pt-4 text-[13.5px] lg:col-span-2">
				{#if run.notes}<p class="whitespace-pre-line">{run.notes}</p>{/if}
				{#if run.description}
					<p class="whitespace-pre-line text-muted-foreground">{run.description}</p>
				{/if}
			</div>
		{/if}
	</section>

	<div class="grid gap-4">
		<label
			class="flex h-9 w-full max-w-80 items-center gap-2 rounded-lg border border-input bg-background px-2.5 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
		>
			<SearchIcon class="size-4 flex-none text-muted-foreground" />
			<input
				class="min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-faint"
				placeholder="Find a config or summary key…"
				aria-label="Find a config or summary key"
				bind:value={query}
			/>
		</label>
		<div class="grid items-start gap-8 lg:grid-cols-2">
			{@render table('Config', shownConfig, config.length)}
			{@render table('Summary', shownSummary, summary.length)}
		</div>
	</div>
</div>
