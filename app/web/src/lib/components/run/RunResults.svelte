<script lang="ts">
	import LockIcon from '@lucide/svelte/icons/lock';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import { RESULT_SCHEMAS } from '#lib/evals.ts';
	import type { EvalResults, RunDetail } from '#lib/types.ts';
	import { formatNumber } from '#lib/utils.ts';

	/** An eval run's results file (025 D184): suites, tasks and what produced them. */
	let { run, results }: { run: RunDetail; results: EvalResults } = $props();

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
</script>

{#snippet hash(value: string)}
	<span class="inline-flex items-center gap-1">
		<span class="font-mono text-[12px] text-muted-foreground" title={value}
			>{value.slice(0, 12)}</span
		>
		<CopyButton text={value} label="Copy hash" class="size-6" />
	</span>
{/snippet}

<div class="absolute inset-0 overflow-y-auto">
	{#if !RESULT_SCHEMAS.includes(results.schema_version)}
		<EmptyState>
			This run’s results file has schema {results.schema_version}; the dashboard reads schemas 8 and
			9.
		</EmptyState>
	{:else}
		<div class="grid max-w-250 gap-8 px-5 pt-6 pb-16 text-[13px]">
			{#if results.suites.length}
				<section aria-labelledby="results-suites">
					<h2 id="results-suites" class="mb-2 text-[15px]">Suites</h2>
					<table class="w-full border-t border-divider">
						<thead class="border-b text-xs text-muted-foreground">
							<tr>
								<th class="px-3 py-2 text-left font-medium">Suite</th>
								<th class="px-3 py-2 text-right font-medium">Score</th>
								<th class="px-3 py-2 text-left font-medium">Metric</th>
								<th class="px-3 py-2 text-right font-medium">Members</th>
								<th class="px-3 py-2 text-left font-medium">Suite hash</th>
							</tr>
						</thead>
						<tbody>
							{#each results.suites as s (s.suite)}
								<tr class="border-b border-divider hover:bg-muted">
									<td class="px-3 py-1.5 font-mono text-[12.5px]">{s.suite}</td>
									<td
										class={['px-3 py-1.5 text-right tabular-nums', s.score == null && 'text-faint']}
										title={s.score == null
											? 'No score: it groups other suites, or its mean isn’t finite'
											: String(s.score)}
									>
										{s.score == null ? '–' : formatNumber(s.score)}
									</td>
									<td class="px-3 py-1.5 text-muted-foreground">
										{s.metric ?? 'each task’s primary metric'}
									</td>
									<td class="px-3 py-1.5 text-right tabular-nums" title={s.members.join(', ')}>
										{s.members.length}
									</td>
									<td class="px-3 py-1.5">{@render hash(s.suite_hash)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</section>
			{/if}

			<section aria-labelledby="results-tasks">
				<h2 id="results-tasks" class="mb-2 text-[15px]">Tasks</h2>
				<div class="overflow-x-auto">
					<table class="w-full border-t border-divider">
						<thead class="border-b text-xs text-muted-foreground">
							<tr>
								<th class="px-3 py-2 text-left font-medium">Task</th>
								<th class="px-3 py-2 text-right font-medium">Score</th>
								<th class="px-3 py-2 text-left font-medium">Primary metric</th>
								<th class="px-3 py-2 text-right font-medium">Docs</th>
								<th class="px-3 py-2 text-left font-medium">Task hash</th>
								<th class="px-3 py-2 text-left font-medium">Dataset</th>
								<th class="px-3 py-2 text-right font-medium">Docs cut</th>
							</tr>
						</thead>
						<tbody>
							{#each results.tasks as t (t.task)}
								{@const value = t.metrics[t.primary_metric]}
								<tr class="border-b border-divider hover:bg-muted">
									<td class="px-3 py-1.5 font-mono text-[12.5px]">{t.task}</td>
									<td
										class={['px-3 py-1.5 text-right tabular-nums', value == null && 'text-faint']}
										title={value == null ? 'Not a finite number' : String(value)}
									>
										{value == null ? '–' : formatNumber(value)}
									</td>
									<td class="px-3 py-1.5 text-muted-foreground">{t.primary_metric}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">
										{t.num_docs}{t.limit != null ? ` (limit ${t.limit})` : ''}
									</td>
									<td class="px-3 py-1.5">{@render hash(t.task_hash)}</td>
									<td
										class="max-w-64 truncate px-3 py-1.5 text-muted-foreground"
										title="{t.dataset.repo}@{t.dataset.revision} {t.dataset.subset ?? ''} {t.dataset
											.split}"
									>
										{#if t.dataset.private}
											<LockIcon class="mr-1 inline size-3.5" aria-label="Private dataset" />
										{/if}
										{t.dataset.repo}{t.dataset.subset ? ` · ${t.dataset.subset}` : ''}
									</td>
									<td
										class={[
											'px-3 py-1.5 text-right tabular-nums',
											!t.truncation.docs && 'text-faint'
										]}
										title="Docs whose context lost tokens to fit the model"
									>
										{t.truncation.docs}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</section>

			<section aria-labelledby="results-provenance">
				<h2 id="results-provenance" class="mb-2 text-[15px]">Provenance</h2>
				<dl class="grid max-w-200 gap-y-1">
					{#each provenance as [label, value] (label)}
						{#if value != null}
							<div class="grid grid-cols-[9rem_minmax(0,1fr)] gap-2">
								<dt class="text-muted-foreground">{label}</dt>
								<dd class="font-mono text-[12.5px] break-all">{String(value)}</dd>
							</div>
						{/if}
					{/each}
				</dl>
				<p class="mt-3 text-muted-foreground">
					Schema {results.schema_version}, from <span class="font-mono">results.json</span> of
					{run.display_name ?? run.id}.
				</p>
			</section>
		</div>
	{/if}
</div>
