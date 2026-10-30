<script lang="ts">
	import { evalMetrics, modelApi, modelCall } from '#lib/models.ts';
	import type { ModelEvalRun, ModelVersionDetail } from '#lib/types.ts';
	import { formatNumber } from '#lib/utils.ts';
	import { runHref } from '#lib/workspace/url.ts';

	let { org, version }: { org: string; version: ModelVersionDetail } = $props();

	let runs = $state<ModelEvalRun[] | null>(null);
	let failed = $state<string | null>(null);

	// The eval runs of this version with their scores, read once the tab opens.
	$effect(() => {
		if (!version.evals.length) return;
		const controller = new AbortController();
		modelCall<{ runs: ModelEvalRun[] }>(
			modelApi(org, version.name, `versions/${version.number}/evals`),
			{ signal: controller.signal }
		)
			.then((got) => (runs = got.runs))
			.catch((e: Error) => {
				if (!controller.signal.aborted) failed = e.message;
			});
		return () => controller.abort();
	});

	const metrics = $derived(runs ? evalMetrics(runs) : []);
	const ref = $derived(`flock:${version.model}@${version.number}`);
</script>

{#if !version.evals.length}
	<div class="max-w-[68ch] bg-card px-4 py-6 text-[13px] text-muted-foreground">
		<p>No eval runs have scored this version yet.</p>
		<p class="mt-1">
			Score it with <code class="font-mono text-[0.95em] text-foreground"
				>flock eval run --model {ref}</code
			>.
		</p>
	</div>
{:else if failed}
	<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{failed}</p>
{:else if runs === null}
	<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
{:else if !runs.length}
	<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">
		The eval runs of this version can’t be read.
	</p>
{:else}
	<div class="overflow-x-auto">
		<table class="w-full border-collapse text-[13px]">
			<thead>
				<tr class="h-10 border-b text-left text-xs text-muted-foreground">
					<th class="px-2 font-medium">Metric</th>
					{#each runs as run (run.path)}
						<th class="px-2 text-right font-medium whitespace-nowrap">
							<a
								class="text-link underline-offset-3 hover:underline"
								href={runHref(run.entity, run.project, run.id)}
								title={run.path}
							>
								{run.display_name ?? run.id}
							</a>
						</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each metrics as metric (metric)}
					<tr class="h-9 border-b border-divider hover:bg-muted">
						<th scope="row" class="px-2 text-left font-mono text-[12.5px] font-normal">
							{metric}
						</th>
						{#each runs as run (run.path)}
							{@const value = run.metrics[metric]}
							<td
								class={['px-2 text-right tabular-nums', value === undefined && 'text-faint']}
								title={value === undefined ? undefined : String(value)}
							>
								{value === undefined ? '–' : formatNumber(value)}
							</td>
						{/each}
					</tr>
				{:else}
					<tr>
						<td class="px-2 py-3 text-muted-foreground" colspan={runs.length + 1}>
							These runs have no scores in their summaries.
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
