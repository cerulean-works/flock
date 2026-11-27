<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import BoxIcon from '@lucide/svelte/icons/box';
	import LockIcon from '@lucide/svelte/icons/lock';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import DataTable, { type Column } from '#lib/components/DataTable.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import InfoHint from '#lib/components/InfoHint.svelte';
	import EvalItems from '#lib/components/run/EvalItems.svelte';
	import { RESULT_SCHEMAS, formatScore } from '#lib/evals.ts';
	import { versionHref } from '#lib/models.ts';
	import type { EvalResults, ItemsView, RunDetail, RunModels } from '#lib/types.ts';
	import { dateTime, relativeTime } from '#lib/utils.ts';

	/**
	 * An eval run's results file (025 D184): suites, tasks and what produced them. `models` names
	 * the version it scored, when Flock linked one (D182).
	 */
	let {
		run,
		results,
		models = null,
		view,
		onview
	}: {
		run: RunDetail;
		results: EvalResults;
		models?: RunModels | null;
		/** Where a link left the items list, and where to tell it moved (028). */
		view?: ItemsView;
		onview?: (view: ItemsView) => void;
	} = $props();

	const p = $derived(results.provenance);
	// What produced the scores, in the order a reader checks two runs against each other.
	const provenance = $derived<[string, string | number | boolean | null | undefined][]>([
		['Model', p.model],
		['Model digest', p.model_digest],
		['Dtype', p.dtype],
		['Batch tokens', p.batch_tokens],
		['Max length', p.max_length],
		['Adds BOS', p.add_bos],
		['Attention', p.attention],
		['SDPA kernel', p.sdpa_backend],
		['Device', p.device],
		['Flock commit', p.flock_commit],
		[
			'Libraries',
			`torch ${p.torch}, transformers ${p.transformers}, tokenizers ${p.tokenizers}, datasets ${p.datasets}`
		],
		['Python', p.python],
		['Scored at', p.created_at],
		['Command', p.command]
	]);

	// The tasks whose docs are right or wrong (eval's `acc_*` metrics) and that have a score,
	// for the items. Docs right are the accuracy times the docs scored.
	const itemTasks = $derived(
		results.tasks
			.filter((t) => t.primary_metric.startsWith('acc_') && t.metrics[t.primary_metric] != null)
			.map((t) => {
				const right = Math.round(t.metrics[t.primary_metric]! * t.num_docs);
				return {
					task: t.task,
					counts: { wrong: t.num_docs - right, right, all: t.num_docs }
				};
			})
	);

	// The version it scored, as a link; a run Flock didn't link shows the model as written.
	const scored = $derived(models?.scored?.[0] ?? null);
	// A column that's all zeros says nothing: shown only when a task lost tokens.
	const truncated = $derived(results.tasks.some((t) => t.truncation.docs > 0));

	// When it was scored: relative, the date and time in the hint.
	const scoredAt = $derived(Date.parse(p.created_at) / 1000);

	const SUITE_COLUMNS: Column[] = [
		{ id: 'name', label: 'Suite' },
		{ id: 'score', label: 'Score', align: 'end' },
		{ id: 'metric', label: 'Metric', size: 'fit' },
		{ id: 'members', label: 'Members', align: 'end' }
	];
	const taskColumns = $derived<Column[]>([
		{ id: 'name', label: 'Task' },
		{ id: 'score', label: 'Score', align: 'end' },
		{ id: 'metric', label: 'Primary metric', size: 'fit' },
		{ id: 'docs', label: 'Docs', align: 'end' },
		{ id: 'dataset', label: 'Dataset', from: 'md' },
		...(truncated ? [{ id: 'cut', label: 'Docs cut', align: 'end' as const }] : [])
	]);
	type Suite = EvalResults['suites'][number];
	type Task = EvalResults['tasks'][number];
</script>

<!-- A suite's or task's name; its hash in the hint, a copy away on hover. -->
{#snippet name(text: string, hash: string)}
	<span class="flex min-w-0 items-center gap-1">
		<span class="truncate" {@attach hint(`${text}\nHash ${hash.slice(0, 12)}`)}>{text}</span>
		<CopyButton
			text={hash}
			label="Copy the hash"
			class="relative z-1 -my-1 size-6 flex-none opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100 [&_svg]:size-3.5"
		/>
	</span>
{/snippet}

{#snippet suiteCell(s: Suite, col: string)}
	{#if col === 'name'}
		{@render name(s.suite, s.suite_hash)}
	{:else if col === 'score'}
		<span
			class={s.score == null ? 'text-faint' : undefined}
			{@attach hint(
				s.score == null
					? 'No score: it groups other suites, or its mean isn’t finite'
					: String(s.score)
			)}>{formatScore(s.score, s.metric)}</span
		>
	{:else if col === 'metric'}
		<span
			class="text-muted-foreground"
			{@attach hint(s.metric ?? 'Each member task’s primary metric')}>{s.metric ?? 'primary'}</span
		>
	{:else}
		<span {@attach hint(s.members.join(', '))}>{s.members.length}</span>
	{/if}
{/snippet}

{#snippet taskCell(t: Task, col: string)}
	{@const value = t.metrics[t.primary_metric]}
	{#if col === 'name'}
		{@render name(t.task, t.task_hash)}
	{:else if col === 'score'}
		<span
			class={value == null ? 'text-faint' : undefined}
			{@attach hint(value == null ? 'Not a finite number' : String(value))}
			>{formatScore(value, t.primary_metric)}</span
		>
	{:else if col === 'metric'}
		<span class="text-muted-foreground">{t.primary_metric}</span>
	{:else if col === 'docs'}
		{t.num_docs}{t.limit != null ? ` (limit ${t.limit})` : ''}
	{:else if col === 'dataset'}
		<span
			class="block truncate text-muted-foreground"
			{@attach hint(
				`${t.dataset.repo}@${t.dataset.revision} ${t.dataset.subset ?? ''} ${t.dataset.split}`
			)}
		>
			{#if t.dataset.private}
				<LockIcon class="mr-1 inline size-3.5" aria-label="Private dataset" />
			{/if}
			{t.dataset.repo}{t.dataset.subset ? ` · ${t.dataset.subset}` : ''}
		</span>
	{:else}
		<span
			class={!t.truncation.docs ? 'text-faint' : undefined}
			{@attach hint('Docs whose context lost tokens to fit the model')}>{t.truncation.docs}</span
		>
	{/if}
{/snippet}

<div class="@container absolute inset-0 overflow-y-auto">
	{#if !RESULT_SCHEMAS.includes(results.schema_version)}
		<EmptyState>
			This run’s results file has schema {results.schema_version}; the dashboard reads schemas
			{RESULT_SCHEMAS.join(', ')}.
		</EmptyState>
	{:else}
		<div class="grid grid-cols-[minmax(0,1fr)] pb-16 text-[13px]">
			<p class="flex flex-wrap items-center gap-x-2 gap-y-1 px-5 pt-5 text-muted-foreground">
				Scored
				{#if scored}
					<a
						class="inline-flex items-center gap-1 text-link underline-offset-3 hover:underline"
						href={versionHref(
							{ org: scored.org ?? run.entity, project: scored.project ?? null },
							scored.name,
							scored.number
						)}
						{@attach hint(`${scored.model}@${scored.number}`)}
					>
						<BoxIcon class="size-3.5" />{scored.project
							? scored.model
							: scored.name}@{scored.number}
					</a>
				{:else}
					<span class="min-w-0 break-all text-foreground">{p.model}</span>
				{/if}
				{#if p.model_digest}
					<span aria-hidden="true">·</span>
					<span class="font-mono text-[12px]" {@attach hint(p.model_digest)}
						>{p.model_digest.slice(0, 12)}</span
					>
				{/if}
				{#if Number.isFinite(scoredAt)}
					<span aria-hidden="true">·</span>
					<span {@attach hint(dateTime(scoredAt))}>{relativeTime(scoredAt, Date.now() / 1000)}</span
					>
				{/if}
			</p>

			{#if results.suites.length}
				<section aria-labelledby="results-suites">
					<h2 id="results-suites" class="px-5 pt-6 pb-2 text-[15px] font-medium">Suites</h2>
					<DataTable
						columns={SUITE_COLUMNS}
						rows={results.suites}
						key={(s) => s.suite}
						cell={suiteCell}
						dense
						label="Suites"
					/>
				</section>
			{/if}

			<section aria-labelledby="results-tasks">
				<h2 id="results-tasks" class="px-5 pt-6 pb-2 text-[15px] font-medium">Tasks</h2>
				<DataTable
					columns={taskColumns}
					rows={results.tasks}
					key={(t) => t.task}
					cell={taskCell}
					dense
					label="Tasks"
				/>
			</section>

			{#if itemTasks.length}
				<section class="px-5 pt-6" aria-labelledby="items-title">
					<!-- Text came with schema 11: an older run's items can't be read, only scored again. -->
					{#if results.schema_version < 11}
						<h2 id="items-title" class="pb-2 text-[15px] font-medium">Items</h2>
						<p class="text-muted-foreground">
							This run’s predictions hold no text (results schema {results.schema_version}). Score
							the model again with this flock to read its items.
						</p>
					{:else}
						<!-- Run ids are per project: a run of another project is another list. -->
						{#key `${run.entity}/${run.project}/${run.id}`}
							<EvalItems
								entity={run.entity}
								project={run.project}
								run={run.id}
								sides={['This run']}
								title="Items"
								bleed
								tasks={itemTasks}
								keeps={[
									{ keep: 'wrong', label: 'Wrong' },
									{ keep: 'right', label: 'Right' },
									{ keep: 'all', label: 'All' }
								]}
								{view}
								{onview}
							/>
						{/key}
					{/if}
				</section>
			{/if}

			<section class="px-5 pt-6" aria-labelledby="results-provenance">
				<h2 id="results-provenance" class="mb-2 flex items-center gap-1.5 text-[15px] font-medium">
					Provenance
					<InfoHint label="About this results file">
						Schema {results.schema_version}, from <span class="font-mono">results.json</span> of
						{run.display_name ?? run.id}.
					</InfoHint>
				</h2>
				<div class="overflow-x-auto">
					<table class="w-full min-w-[52rem] table-fixed border-t border-divider">
						<colgroup>
							<col class="w-[15rem]" />
							<col />
						</colgroup>
						<tbody>
							{#each provenance as [label, value] (label)}
								{#if value != null}
									<tr class="border-b border-divider hover:bg-muted">
										<th scope="row" class="px-3 py-1.5 text-left font-normal text-muted-foreground"
											>{label}</th
										>
										<td class="px-3 py-1.5 font-mono text-[12.5px] break-all">{String(value)}</td>
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>
			</section>
		</div>
	{/if}
</div>
