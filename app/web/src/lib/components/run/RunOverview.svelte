<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { toast } from 'svelte-sonner';
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

	/** Full precision on hover, where the list rounds. */
	const exact = (v: unknown) =>
		typeof v === 'number' ? String(v) : typeof v === 'string' ? v : formatValue(v);

	const gpu = $derived(gpus(run.gpu_type, run.gpu_count));
	/** The GPUs were corrected by hand (Edit run), rather than detected by wandb. */
	const gpuSetByHand = $derived(
		run.gpu_type !== run.gpu_detected.type || run.gpu_count !== run.gpu_detected.count
	);

	async function copy(label: string, text: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast(`Copied the ${label}`);
		} catch {
			toast.error('Couldn’t copy to the clipboard');
		}
	}
</script>

<!-- Label and value on one row: the value takes what the label leaves. -->
{#snippet field(label: string, value: string | null | undefined, title?: string)}
	{#if value}
		<div class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2 py-1">
			<dt class="text-muted-foreground">{label}</dt>
			<dd class="min-w-0 break-words" {title}>{value}</dd>
		</div>
	{/if}
{/snippet}

<!-- Long, unbreakable values (commands, hashes) under their label, with a copy button. -->
{#snippet code(label: string, value: string | null | undefined)}
	{#if value}
		<div class="group/code py-1">
			<dt class="flex items-center justify-between text-muted-foreground">
				{label}
				<button
					class="rounded p-0.5 text-faint opacity-0 group-hover/code:opacity-100 hover:text-foreground focus-visible:opacity-100"
					title="Copy the {label.toLowerCase()}"
					aria-label="Copy the {label.toLowerCase()}"
					onclick={() => copy(label.toLowerCase(), value)}
				>
					<CopyIcon class="size-3.5" />
				</button>
			</dt>
			<dd
				class="mt-0.5 line-clamp-4 rounded-md bg-muted/60 px-2 py-1 font-mono text-[12px] leading-[1.45] break-all"
				title={value}
			>
				{value}
			</dd>
		</div>
	{/if}
{/snippet}

{#snippet list(title: string, rows: [string, unknown][], total: number)}
	<details class="group/list border-t border-divider" open>
		<summary
			class="flex cursor-pointer list-none items-center gap-1.5 px-4 py-2.5 select-none hover:bg-muted/60 [&::-webkit-details-marker]:hidden"
		>
			<ChevronRightIcon
				class="size-3.5 text-muted-foreground transition-transform group-open/list:rotate-90"
			/>
			<span class="font-medium">{title}</span>
			<span class="ml-auto text-xs text-muted-foreground tabular-nums">
				{rows.length === total ? total : `${rows.length} of ${total}`}
			</span>
		</summary>
		<dl class="pb-3 font-mono text-[12px] leading-5">
			{#each rows as [key, value] (key)}
				{@const shown = formatValue(value)}
				<div class="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 px-4 hover:bg-muted">
					<dt class="truncate text-muted-foreground" title={key}>{key}</dt>
					<dd
						class={['truncate text-right tabular-nums', shown === '–' && 'text-faint']}
						title={exact(value)}
					>
						{shown}
					</dd>
				</div>
			{:else}
				<p class="px-4 font-sans text-[13px] text-muted-foreground">
					{total ? 'No matching keys' : 'Nothing logged'}
				</p>
			{/each}
		</dl>
	</details>
{/snippet}

<div class="text-[13px]">
	<dl class="px-4 pt-3 pb-4">
		<div class="grid grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-2 py-1">
			<dt class="text-muted-foreground">State</dt>
			<dd><StateBadge state={runState} /></dd>
		</div>
		<div class="grid grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-2 py-1">
			<dt class="text-muted-foreground">ID</dt>
			<dd class="flex min-w-0 items-center gap-1">
				<span class="run-id truncate font-mono text-[12px]" title={run.id}>{run.id}</span>
				<button
					class="flex-none rounded p-0.5 text-faint hover:text-foreground"
					title="Copy the run id"
					aria-label="Copy the run id"
					onclick={() => copy('run id', run.id)}
				>
					<CopyIcon class="size-3.5" />
				</button>
			</dd>
		</div>
		<div class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2 py-1">
			<dt class="text-muted-foreground">Started</dt>
			<dd title={dateTime(run.created_at)}>
				{relativeTime(run.created_at, now)}
				<span class="block text-xs text-muted-foreground">{dateTime(run.created_at)}</span>
			</dd>
		</div>
		{@render field('Runtime', duration(run.runtime))}
		<div class="grid grid-cols-[6.5rem_minmax(0,1fr)] items-center gap-2 py-1">
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
		{@render field('GPUs', gpu && gpuSetByHand ? `${gpu} · set by hand` : gpu)}
		{@render field(
			'GPU hours',
			run.gpu_hours
				? `${hours(run.gpu_hours)}${currency ? ` · ${money(run.cost, currency)}` : ''}`
				: null
		)}
		{@render field('Host', run.host)}
		{@render field('Group', run.group)}
		{@render field('Job type', run.job_type)}
		{@render field('Sweep', run.sweep)}
		{@render field('Exit code', run.exitcode == null ? null : String(run.exitcode))}
		{#if run.tags.length}
			<div class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-2 py-1">
				<dt class="text-muted-foreground">Tags</dt>
				<dd class="flex min-w-0 flex-wrap gap-1">
					{#each run.tags as tag (tag)}
						<Badge variant="secondary" class="max-w-full" title={tag}>
							<span class="truncate">{tag}</span>
						</Badge>
					{/each}
				</dd>
			</div>
		{/if}
		{@render code('Command', run.program)}
		{@render code('Commit', run.commit)}
	</dl>

	{#if run.notes || run.description}
		<div class="grid gap-2 border-t border-divider px-4 py-3">
			{#if run.notes}<p class="break-words whitespace-pre-line">{run.notes}</p>{/if}
			{#if run.description}
				<p class="break-words whitespace-pre-line text-muted-foreground">{run.description}</p>
			{/if}
		</div>
	{/if}

	{#if config.length || summary.length}
		<div class="border-t border-divider px-4 py-2.5">
			<label
				class="flex h-7 w-full items-center gap-2 rounded-[min(var(--radius-md),10px)] border border-input bg-background px-2 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50"
			>
				<SearchIcon class="size-3.5 flex-none text-muted-foreground" />
				<input
					class="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint"
					placeholder="Find a config or summary key"
					aria-label="Find a config or summary key"
					bind:value={query}
					onkeydown={(e) => e.key === 'Escape' && (query = '')}
				/>
			</label>
		</div>
	{/if}
	{@render list('Config', shownConfig, config.length)}
	{@render list('Summary', shownSummary, summary.length)}
</div>
