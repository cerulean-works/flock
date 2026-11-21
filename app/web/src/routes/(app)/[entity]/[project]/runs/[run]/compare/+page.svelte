<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { SvelteSet } from 'svelte/reactivity';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { NOT_COMPARED } from '#lib/evals.ts';
	import type { RunDetail } from '#lib/types.ts';
	import { runHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// Tasks whose doc ids are shown, by name.
	const open = new SvelteSet<string>();

	const totals = $derived.by(() => {
		const sum = { docs: 0, both_right: 0, both_wrong: 0, only_a: 0, only_b: 0 };
		for (const t of data.diff?.tasks ?? []) {
			sum.docs += t.num_docs;
			sum.both_right += t.both_right;
			sum.both_wrong += t.both_wrong;
			sum.only_a += t.only_a.length;
			sum.only_b += t.only_b.length;
		}
		return sum;
	});
</script>

<svelte:head>
	<title>Compare items · {data.a.display_name ?? data.a.id}</title>
</svelte:head>

{#snippet side(label: string, run: RunDetail, model: string | undefined)}
	<div class="min-w-0 bg-card px-4 py-3">
		<p class="text-xs text-muted-foreground">{label}</p>
		<a
			class="block truncate text-[15px] text-link underline-offset-3 hover:underline"
			href={runHref(run.entity, run.project, run.id)}
		>
			{run.display_name ?? run.id}
		</a>
		<p class="truncate font-mono text-[12px] text-muted-foreground" title={model}>
			{run.entity}/{run.project}/{run.id}{model ? ` · ${model}` : ''}
		</p>
	</div>
{/snippet}

{#snippet ids(label: string, list: string[])}
	<div class="min-w-0">
		<p class="mb-1 flex items-center gap-1 text-xs text-muted-foreground">
			{label} ({list.length})
			{#if list.length}<CopyButton text={list.join('\n')} label="Copy ids" class="size-6" />{/if}
		</p>
		<p class="max-h-40 overflow-y-auto font-mono text-[12px] break-all">
			{list.length ? list.join(' ') : '–'}
		</p>
	</div>
{/snippet}

<Page width="home">
	<PageHeader title="Compare items">
		{#snippet subtitle()}
			Which docs each run got right, on the tasks both scored the same way
		{/snippet}
	</PageHeader>

	<div class="grid gap-6 pb-16 text-[13px]">
		<div class="grid gap-2 sm:grid-cols-2">
			{@render side('A', data.a, data.diff?.a)}
			{@render side('B', data.b, data.diff?.b)}
		</div>

		{#if data.refusal}
			<p class="bg-card px-3 py-2.5" role="alert">
				These runs can’t be compared doc by doc: {data.refusal}
			</p>
		{:else if data.diff}
			<section aria-labelledby="compared">
				<h2 id="compared" class="mb-2 text-[22px] tracking-[-0.01em]">
					{data.diff.tasks.length} tasks compared
				</h2>
				<div class="overflow-x-auto">
					<table class="w-full border-t border-divider">
						<thead class="border-b text-xs text-muted-foreground">
							<tr>
								<th class="px-3 py-2 text-left font-medium">Task</th>
								<th class="px-3 py-2 text-right font-medium">Docs</th>
								<th class="px-3 py-2 text-right font-medium">Both right</th>
								<th class="px-3 py-2 text-right font-medium">Both wrong</th>
								<th class="px-3 py-2 text-right font-medium">Only A right</th>
								<th class="px-3 py-2 text-right font-medium">Only B right</th>
							</tr>
						</thead>
						<tbody>
							{#each data.diff.tasks as t (t.task)}
								{@const shown = open.has(t.task)}
								<tr class="border-b border-divider hover:bg-muted">
									<td class="px-3 py-1.5">
										<button
											type="button"
											class="flex items-center gap-1 font-mono text-[12.5px]"
											aria-expanded={shown}
											onclick={() => (shown ? open.delete(t.task) : open.add(t.task))}
										>
											<ChevronRightIcon class={['size-3.5 transition-transform', shown && 'rotate-90']} />
											{t.task}
										</button>
										<span class="ml-5 text-xs text-faint" title={t.task_hash}>
											{t.metric} · {t.task_hash.slice(0, 12)}
										</span>
									</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.num_docs}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.both_right}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.both_wrong}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.only_a.length}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.only_b.length}</td>
								</tr>
								{#if shown}
									<tr class="border-b border-divider">
										<td colspan="6" class="px-3 py-3">
											<div class="grid gap-4 sm:grid-cols-2">
												{@render ids('Only A right', t.only_a)}
												{@render ids('Only B right', t.only_b)}
											</div>
										</td>
									</tr>
								{/if}
							{/each}
						</tbody>
						{#if data.diff.tasks.length > 1}
							<tfoot class="text-muted-foreground">
								<tr>
									<td class="px-3 py-2">All compared tasks</td>
									<td class="px-3 py-2 text-right tabular-nums">{totals.docs}</td>
									<td class="px-3 py-2 text-right tabular-nums">{totals.both_right}</td>
									<td class="px-3 py-2 text-right tabular-nums">{totals.both_wrong}</td>
									<td class="px-3 py-2 text-right tabular-nums">{totals.only_a}</td>
									<td class="px-3 py-2 text-right tabular-nums">{totals.only_b}</td>
								</tr>
							</tfoot>
						{/if}
					</table>
				</div>
				<p class="mt-2 text-muted-foreground">
					Doc ids are the dataset’s own ids, or a hash of the doc where it has none. Counts only: no
					test of significance.
				</p>
			</section>

			{#if data.diff.not_compared.length}
				<section aria-labelledby="not-compared">
					<h2 id="not-compared" class="mb-2 text-[22px] tracking-[-0.01em]">
						{data.diff.not_compared.length} tasks not compared
					</h2>
					<table class="w-full border-t border-divider">
						<tbody>
							{#each data.diff.not_compared as [task, reason] (task)}
								<tr class="border-b border-divider">
									<td class="px-3 py-1.5 font-mono text-[12.5px]">{task}</td>
									<td class="px-3 py-1.5 text-muted-foreground">{NOT_COMPARED[reason] ?? reason}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</section>
			{/if}
		{/if}
	</div>
</Page>
