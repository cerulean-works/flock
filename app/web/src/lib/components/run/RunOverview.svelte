<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import SearchIcon from '@lucide/svelte/icons/search';
	import XIcon from '@lucide/svelte/icons/x';
	import { tick } from 'svelte';
	import Avatar from '#lib/components/Avatar.svelte';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import Properties from '#lib/components/Properties.svelte';
	import Property from '#lib/components/Property.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import TagChip from '#lib/components/TagChip.svelte';
	import { flatten } from '#lib/data/run.ts';
	import type { RunDetail, RunModels } from '#lib/types.ts';
	import RunModelsList from './RunModels.svelte';
	import { dateTime, duration, formatValue, gpus, hours, money, relativeTime } from '#lib/utils.ts';

	interface Props {
		run: RunDetail;
		/** Its state, as the live stream reports it. */
		runState: string;
		owner: { name: string; avatar_url: string | null } | null;
		/** The org's, for the run's cost; null outside the org, where there's no cost (D113). */
		currency: string | null;
		/** The model versions it made and scored; null when they can't be read. */
		models?: RunModels | null;
		now: number;
		/** Opens the Logs tab, where a failed run's error is. */
		onlogs?: () => void;
	}

	let { run, runState, owner, currency, models = null, now, onlogs }: Props = $props();

	const failed = $derived(runState === 'failed' || runState === 'crashed');

	const config = $derived(flatten(run.config).sort(([a], [b]) => a.localeCompare(b)));
	// wandb's bookkeeping (`_step`, `_runtime`, ...) is in the details already.
	const summary = $derived(
		flatten(run.summary)
			.filter(([k]) => !k.startsWith('_'))
			.sort(([a], [b]) => a.localeCompare(b))
	);

	/** Full precision on hover, where the list rounds. */
	const exact = (v: unknown) =>
		typeof v === 'number' ? String(v) : typeof v === 'string' ? v : formatValue(v);

	const gpu = $derived(gpus(run.gpu_type, run.gpu_count));
	/** The GPUs were corrected by hand (Edit run), rather than detected by wandb. */
	const gpuSetByHand = $derived(
		run.gpu_type !== run.gpu_detected.type || run.gpu_count !== run.gpu_detected.count
	);

	// Each list's search: an icon on its row that opens a field in the title's place.
	const queries = $state<Record<string, string | null>>({ Config: null, Summary: null });
	const match = (rows: [string, unknown][], q: string | null) => {
		const t = q?.trim().toLowerCase();
		return t ? rows.filter(([k]) => k.toLowerCase().includes(t)) : rows;
	};

	async function openSearch(title: string, details: HTMLDetailsElement | null) {
		queries[title] = '';
		if (details) details.open = true;
		await tick();
		document.getElementById(`find-${title}`)?.focus();
	}
</script>

<!-- Copy on hover, at the row's end. -->
{#snippet copy(text: string, label: string)}
	<CopyButton
		{text}
		{label}
		class="-my-1 size-6 flex-none opacity-0 group-hover/prop:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100 [&_svg]:size-3.5"
	/>
{/snippet}

{#snippet list(title: string, all: [string, unknown][])}
	{@const q = queries[title]}
	{@const rows = match(all, q)}
	{#if all.length}
		<Properties label={title}>
			<details class="group/list" open>
				<summary
					class="-mx-2 flex h-8 cursor-pointer list-none items-center gap-1.5 rounded-md px-2 text-[13px] select-none hover:bg-muted/60 [&::-webkit-details-marker]:hidden"
				>
					{#if q == null}
						<ChevronRightIcon
							class="size-3.5 flex-none text-muted-foreground transition-transform group-open/list:rotate-90"
						/>
						<span class="font-medium">{title}</span>
						<span class="text-muted-foreground tabular-nums">{all.length}</span>
						<button
							type="button"
							class="-mr-1 ml-auto grid size-6 place-items-center rounded text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
							aria-label="Find a {title.toLowerCase()} key"
							{@attach hint(`Find a ${title.toLowerCase()} key`)}
							onclick={(e) => {
								e.preventDefault();
								void openSearch(title, e.currentTarget.closest('details'));
							}}
						>
							<SearchIcon class="size-3.5" />
						</button>
					{:else}
						<SearchIcon class="size-3.5 flex-none text-muted-foreground" />
						<input
							id="find-{title}"
							class="min-w-0 flex-1 bg-transparent outline-none placeholder:text-faint"
							placeholder="Find a {title.toLowerCase()} key"
							aria-label="Find a {title.toLowerCase()} key"
							bind:value={queries[title]}
							onclick={(e) => e.preventDefault()}
							onkeydown={(e) => {
								// Space would toggle the list.
								if (e.key === ' ') e.stopPropagation();
								if (e.key === 'Escape') {
									e.stopPropagation();
									queries[title] = null;
								}
							}}
							onkeyup={(e) => e.key === ' ' && e.preventDefault()}
						/>
						<span class="text-muted-foreground tabular-nums">{rows.length}</span>
						<button
							type="button"
							class="-mr-1 grid size-6 place-items-center rounded text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
							aria-label="Close the search"
							onclick={(e) => {
								e.preventDefault();
								queries[title] = null;
							}}
						>
							<XIcon class="size-3.5" />
						</button>
					{/if}
				</summary>
				<dl class="pt-1 pb-1 font-mono text-[12px] leading-6">
					{#each rows as [key, value] (key)}
						{@const shown = formatValue(value)}
						<div
							class="-mx-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 rounded px-2 hover:bg-muted"
						>
							<dt class="truncate text-muted-foreground" {@attach hint(key)}>{key}</dt>
							<dd
								class={['truncate text-right tabular-nums', shown === '–' && 'text-faint']}
								{@attach hint(exact(value))}
							>
								{shown}
							</dd>
						</div>
					{:else}
						<p class="py-1 font-sans text-[13px] text-muted-foreground">No matching keys</p>
					{/each}
				</dl>
			</details>
		</Properties>
	{/if}
{/snippet}

<!-- The run's state first: a failed one leads with its exit code and the way to its error. -->
<Properties label="Run">
	<Property label="State">
		<span class="flex flex-wrap items-baseline gap-x-2">
			<span class="flex items-baseline gap-1.5">
				<StateBadge state={runState} />
				{#if run.exitcode != null && run.exitcode !== 0}
					<span class="text-muted-foreground">· exit {run.exitcode}</span>
				{/if}
			</span>
			{#if failed && onlogs}
				<button
					type="button"
					class="rounded-sm text-link underline-offset-3 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
					onclick={onlogs}>See logs</button
				>
			{/if}
		</span>
	</Property>
	{#if run.notes}
		<Property label="Notes" stack>
			<p class="whitespace-pre-line">{run.notes}</p>
		</Property>
	{/if}
	{#if run.description}
		<Property label="Description" stack>
			<p class="whitespace-pre-line text-muted-foreground">{run.description}</p>
		</Property>
	{/if}
</Properties>

<Properties label="Details">
	<Property label="ID" mono class="group/prop">
		<span class="flex min-w-0 items-center gap-1">
			<span class="run-id truncate">{run.id}</span>
			{@render copy(run.id, 'Copy the run id')}
		</span>
	</Property>
	<Property label="Started">
		<span {@attach hint(relativeTime(run.created_at, now))}>{dateTime(run.created_at)}</span>
	</Property>
	{#if run.runtime}<Property label="Runtime">{duration(run.runtime)}</Property>{/if}
	<Property label="Logged by">
		{#if owner}
			<span class="flex min-w-0 items-center gap-2">
				<Avatar
					name={owner.name}
					src={owner.avatar_url}
					class="size-4 flex-none self-center text-[8px]"
				/>
				<span class="truncate">{owner.name}</span>
			</span>
		{:else}
			<span class="text-faint">–</span>
		{/if}
	</Property>
	{#if gpu}
		<Property label="GPUs">
			{gpu}
			{#if gpuSetByHand}<span class="text-muted-foreground">&nbsp;· set by hand</span>{/if}
		</Property>
	{/if}
	{#if run.gpu_hours}
		<Property label="GPU hours">
			{hours(run.gpu_hours)}
			{#if currency && run.cost != null}<span class="text-muted-foreground"
					>&nbsp;· {money(run.cost, currency)}</span
				>{/if}
		</Property>
	{/if}
	{#if run.host}<Property label="Host">{run.host}</Property>{/if}
	{#if run.group}<Property label="Group">{run.group}</Property>{/if}
	{#if run.job_type}<Property label="Job type">{run.job_type}</Property>{/if}
	{#if run.sweep}<Property label="Sweep">{run.sweep}</Property>{/if}
	{#if run.tags.length}
		<Property label={run.tags.length === 1 ? 'Tag' : 'Tags'}>
			<ul class="flex flex-wrap gap-1" aria-label="Tags">
				{#each run.tags as tag (tag)}
					<TagChip as="li" {tag} />
				{/each}
			</ul>
		</Property>
	{/if}
	{#if run.program}
		<Property label="Command" mono class="group/prop">
			<span class="flex min-w-0 items-start gap-1">
				<span class="min-w-0 break-all">{run.program}</span>
				{@render copy(run.program, 'Copy the command')}
			</span>
		</Property>
	{/if}
	{#if run.commit}
		<Property label="Commit" mono class="group/prop">
			<span class="flex min-w-0 items-center gap-1">
				<span class="truncate" {@attach hint(run.commit)}>{run.commit.slice(0, 12)}</span>
				{@render copy(run.commit, 'Copy the commit')}
			</span>
		</Property>
	{/if}
</Properties>

{#if models?.made.length}
	<Properties label="Checkpoints">
		<RunModelsList title="Checkpoints" versions={models.made} />
	</Properties>
{/if}
<!-- An API from before 025 answers no `scored`. -->
{#if models?.scored?.length}
	<Properties label="Models scored">
		<RunModelsList title="Models scored" versions={models.scored} />
	</Properties>
{/if}

{@render list('Config', config)}
{@render list('Summary', summary)}
