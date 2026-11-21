<script lang="ts">
	import GitCompareArrowsIcon from '@lucide/svelte/icons/git-compare-arrows';
	import { SvelteSet } from 'svelte/reactivity';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { compareHref, evalRows } from '#lib/evals.ts';
	import { modelApi, modelCall } from '#lib/models.ts';
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

	const rows = $derived(runs ? evalRows(runs) : []);
	// A name on two rows was scored two ways: its hash tells the rows apart.
	const repeated = $derived(
		new Set(
			rows.filter((r, i) => rows.findIndex((o) => o.name === r.name) !== i).map((r) => r.name)
		)
	);
	const ref = $derived(`flock:${version.model}@${version.number}`);

	// Two checked runs, in the table's order, are compared doc by doc (023 D174).
	const checked = new SvelteSet<string>();
	const pair = $derived(runs?.filter((r) => checked.has(r.path)) ?? []);
</script>

{#if !version.evals.length}
	<div class="max-w-[68ch] bg-card px-4 py-6 text-[13px] text-muted-foreground">
		<p>No eval runs have scored this version yet.</p>
		<p class="mt-1">
			Score it with <code class="font-mono text-[0.95em] text-foreground"
				>flock eval run --model {ref} --wandb-project model-evals</code
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
	<div class="mb-3 flex min-h-9 items-center gap-3 text-[13px] text-muted-foreground">
		{#if pair.length === 2}
			<Button
				variant="outline"
				href={compareHref(pair[0].entity, pair[0].project, pair[0].id, pair[1].path)}
			>
				<GitCompareArrowsIcon /> Compare items
			</Button>
		{:else}
			<span>Check two runs to compare them doc by doc.</span>
		{/if}
	</div>
	<div class="overflow-x-auto">
		<table class="w-full border-collapse text-[13px]">
			<thead>
				<tr class="h-10 border-b text-left text-xs text-muted-foreground">
					<th class="px-2 font-medium">Score</th>
					{#each runs as run (run.path)}
						<th class="px-2 text-right font-medium whitespace-nowrap">
							<span class="inline-flex items-center gap-2">
								<Checkbox
									checked={checked.has(run.path)}
									disabled={!checked.has(run.path) && checked.size >= 2}
									onCheckedChange={(on) => (on ? checked.add(run.path) : checked.delete(run.path))}
									aria-label="Compare {run.display_name ?? run.id}"
								/>
								<a
									class="text-link underline-offset-3 hover:underline"
									href={runHref(run.entity, run.project, run.id)}
									title={run.path}
								>
									{run.display_name ?? run.id}
								</a>
							</span>
						</th>
					{/each}
				</tr>
			</thead>
			<tbody>
				{#each rows as row (row.key)}
					<tr class="h-9 border-b border-divider hover:bg-muted">
						<th scope="row" class="px-2 text-left font-normal">
							<span class="font-mono text-[12.5px]">{row.name}</span>
							<span class="ml-1.5 text-xs text-faint" title={row.hash}>
								{row.kind === 'suite' ? 'suite' : row.metric}{repeated.has(row.name)
									? ` · ${row.hash.slice(0, 12)}`
									: ''}
							</span>
						</th>
						{#each runs as run (run.path)}
							{@const value = run.scores?.values[row.key]}
							<td
								class={['px-2 text-right tabular-nums', value == null && 'text-faint']}
								title={value == null ? undefined : String(value)}
							>
								{value == null ? '–' : formatNumber(value)}
							</td>
						{/each}
					</tr>
				{:else}
					<tr>
						<td class="px-2 py-3 text-muted-foreground" colspan={runs.length + 1}>
							These runs have no results file, so there are no scores to show.
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
