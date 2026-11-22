<script lang="ts">
	import BoxIcon from '@lucide/svelte/icons/box';
	import LockIcon from '@lucide/svelte/icons/lock';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import InfoHint from '#lib/components/InfoHint.svelte';
	import EvalItems from '#lib/components/run/EvalItems.svelte';
	import { RESULT_SCHEMAS, formatScore } from '#lib/evals.ts';
	import { versionHref } from '#lib/models.ts';
	import type { EvalResults, RunDetail, RunModels } from '#lib/types.ts';

	/**
	 * An eval run's results file (025 D184): suites, tasks and what produced them. `models` names
	 * the version it scored, when Flock linked one (D182).
	 */
	let {
		run,
		results,
		models = null
	}: { run: RunDetail; results: EvalResults; models?: RunModels | null } = $props();

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
</script>

{#snippet hash(value: string)}
	<span class="inline-flex items-center gap-1">
		<span class="font-mono text-[12px] text-muted-foreground" title={value}
			>{value.slice(0, 12)}</span
		>
		<CopyButton text={value} label="Copy hash" class="size-6" />
	</span>
{/snippet}

<!--
	Both tables share the first five columns' widths, so a suite's score sits above its tasks'
	scores; the rest of the width goes to the tasks' datasets.
-->
{#snippet columns()}
	<colgroup>
		<col class="w-[15rem]" />
		<col class="w-[5.5rem]" />
		<col class="w-[8rem]" />
		<col class="w-[5rem]" />
		<col class="w-[8.5rem]" />
		<col />
		{#if truncated}<col class="w-[6rem]" />{/if}
	</colgroup>
{/snippet}

<div class="absolute inset-0 overflow-y-auto">
	{#if !RESULT_SCHEMAS.includes(results.schema_version)}
		<EmptyState>
			This run’s results file has schema {results.schema_version}; the dashboard reads schemas
			{RESULT_SCHEMAS.join(', ')}.
		</EmptyState>
	{:else}
		<div class="grid grid-cols-[minmax(0,1fr)] gap-8 px-5 pt-5 pb-16 text-[13px]">
			<p class="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
				Scored
				{#if scored}
					<a
						class="inline-flex items-center gap-1 text-link underline-offset-3 hover:underline"
						href={versionHref(
							{ org: scored.org ?? run.entity, project: scored.project ?? null },
							scored.name,
							scored.number
						)}
						title="{scored.model}@{scored.number}"
					>
						<BoxIcon class="size-3.5" />{scored.project
							? scored.model
							: scored.name}@{scored.number}
					</a>
				{:else}
					<span class="font-mono text-[12.5px] text-foreground">{p.model}</span>
				{/if}
				{#if p.model_digest}
					<span aria-hidden="true">·</span>
					<span class="font-mono text-[12px]" title={p.model_digest}
						>{p.model_digest.slice(0, 12)}</span
					>
				{/if}
				<span aria-hidden="true">·</span>
				<span>{p.created_at}</span>
			</p>

			{#if results.suites.length}
				<section aria-labelledby="results-suites">
					<h2 id="results-suites" class="mb-2 text-[15px]">Suites</h2>
					<div class="overflow-x-auto">
						<table class="w-full min-w-[52rem] table-fixed border-t border-divider">
							{@render columns()}
							<thead class="border-b text-xs text-muted-foreground">
								<tr>
									<th class="px-3 py-2 text-left font-medium">Suite</th>
									<th class="px-3 py-2 text-right font-medium">Score</th>
									<th class="px-3 py-2 text-left font-medium">Metric</th>
									<th class="px-3 py-2 text-right font-medium">Members</th>
									<th class="px-3 py-2 text-left font-medium">Hash</th>
									<th></th>
									{#if truncated}<th></th>{/if}
								</tr>
							</thead>
							<tbody>
								{#each results.suites as s (s.suite)}
									<tr class="border-b border-divider hover:bg-muted">
										<td class="truncate px-3 py-1.5 font-mono text-[12.5px]" title={s.suite}
											>{s.suite}</td
										>
										<td
											class={[
												'px-3 py-1.5 text-right tabular-nums',
												s.score == null && 'text-faint'
											]}
											title={s.score == null
												? 'No score: it groups other suites, or its mean isn’t finite'
												: String(s.score)}
										>
											{formatScore(s.score)}
										</td>
										<td
											class="truncate px-3 py-1.5 text-muted-foreground"
											title={s.metric ?? 'Each member task’s primary metric'}
										>
											{s.metric ?? 'primary'}
										</td>
										<td class="px-3 py-1.5 text-right tabular-nums" title={s.members.join(', ')}>
											{s.members.length}
										</td>
										<td class="px-3 py-1.5">{@render hash(s.suite_hash)}</td>
										<td></td>
										{#if truncated}<td></td>{/if}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>
			{/if}

			<section aria-labelledby="results-tasks">
				<h2 id="results-tasks" class="mb-2 text-[15px]">Tasks</h2>
				<div class="overflow-x-auto">
					<table class="w-full min-w-[52rem] table-fixed border-t border-divider">
						{@render columns()}
						<thead class="border-b text-xs text-muted-foreground">
							<tr>
								<th class="px-3 py-2 text-left font-medium">Task</th>
								<th class="px-3 py-2 text-right font-medium">Score</th>
								<th class="px-3 py-2 text-left font-medium">Primary metric</th>
								<th class="px-3 py-2 text-right font-medium">Docs</th>
								<th class="px-3 py-2 text-left font-medium">Hash</th>
								<th class="px-3 py-2 text-left font-medium">Dataset</th>
								{#if truncated}
									<th class="px-3 py-2 text-right font-medium">Docs cut</th>
								{/if}
							</tr>
						</thead>
						<tbody>
							{#each results.tasks as t (t.task)}
								{@const value = t.metrics[t.primary_metric]}
								<tr class="border-b border-divider hover:bg-muted">
									<td class="truncate px-3 py-1.5 font-mono text-[12.5px]" title={t.task}
										>{t.task}</td
									>
									<td
										class={['px-3 py-1.5 text-right tabular-nums', value == null && 'text-faint']}
										title={value == null ? 'Not a finite number' : String(value)}
									>
										{formatScore(value)}
									</td>
									<td class="truncate px-3 py-1.5 text-muted-foreground" title={t.primary_metric}
										>{t.primary_metric}</td
									>
									<td class="px-3 py-1.5 text-right tabular-nums">
										{t.num_docs}{t.limit != null ? ` (limit ${t.limit})` : ''}
									</td>
									<td class="px-3 py-1.5">{@render hash(t.task_hash)}</td>
									<td
										class="truncate px-3 py-1.5 text-muted-foreground"
										title="{t.dataset.repo}@{t.dataset.revision} {t.dataset.subset ?? ''} {t.dataset
											.split}"
									>
										{#if t.dataset.private}
											<LockIcon class="mr-1 inline size-3.5" aria-label="Private dataset" />
										{/if}
										{t.dataset.repo}{t.dataset.subset ? ` · ${t.dataset.subset}` : ''}
									</td>
									{#if truncated}
										<td
											class={[
												'px-3 py-1.5 text-right tabular-nums',
												!t.truncation.docs && 'text-faint'
											]}
											title="Docs whose context lost tokens to fit the model"
										>
											{t.truncation.docs}
										</td>
									{/if}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>

			{#if itemTasks.length}
				<section aria-labelledby="results-items">
					<h2 id="results-items" class="mb-2 text-[15px]">Items</h2>
					<!-- Text came with schema 11: an older run's items can't be read, only scored again. -->
					{#if results.schema_version < 11}
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
								tasks={itemTasks}
								keeps={[
									{ keep: 'wrong', label: 'Wrong' },
									{ keep: 'right', label: 'Right' },
									{ keep: 'all', label: 'All' }
								]}
							/>
						{/key}
					{/if}
				</section>
			{/if}

			<section aria-labelledby="results-provenance">
				<h2 id="results-provenance" class="mb-2 flex items-center gap-1.5 text-[15px]">
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
