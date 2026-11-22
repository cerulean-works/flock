<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import Breadcrumbs from '#lib/components/Breadcrumbs.svelte';
	import CopyButton from '#lib/components/CopyButton.svelte';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import InfoHint from '#lib/components/InfoHint.svelte';
	import EvalItems from '#lib/components/run/EvalItems.svelte';
	import EvalRunPicker from '#lib/components/run/EvalRunPicker.svelte';
	import { NOT_COMPARED, compareHref } from '#lib/evals.ts';
	import type { ItemKeep, RunDetail } from '#lib/types.ts';
	import { projectHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// The items' filters: each task with its four cells' counts.
	const tasks = $derived(
		(data.diff?.tasks ?? []).map((t) => ({
			task: t.task,
			counts: {
				only_a: t.only_a.length,
				only_b: t.only_b.length,
				both_wrong: t.both_wrong,
				both_right: t.both_right
			}
		}))
	);

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

	const pathOf = (r: RunDetail) => `${r.entity}/${r.project}/${r.id}`;
	const nameOf = (r: RunDetail) => r.display_name ?? r.id;

	// Either side changes in place: the page is the pair's URL, so it's also what gets linked.
	const show = (a: RunDetail | [string, string, string], b: string) => {
		const [e, p, r] = Array.isArray(a) ? a : [a.entity, a.project, a.id];
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- compareHref resolves it
		void goto(compareHref(e, p, r, b), { reset: false });
	};

	// The pickers open from the keyboard too: A and B.
	let pickingA = $state(false);
	let pickingB = $state(false);
	// Opened with A alone (the run page's Compare with…): B's list is open, ready to type in.
	onMount(() => {
		if (!data.b) pickingB = true;
	});
	function onkeydown(e: KeyboardEvent) {
		if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
		const target = e.target as HTMLElement | null;
		// A select's own keys (the task select's typeahead) aren't the page's.
		const own = 'input, textarea, select, [role=combobox], [role=listbox], [contenteditable=""]';
		if (target?.closest(`${own}, [contenteditable=true]`)) return;
		if (pickingA || pickingB) return;
		const key = e.key.toLowerCase();
		if (key === 'a') pickingA = true;
		else if (key === 'b') pickingB = true;
		else return;
		e.preventDefault();
	}

	// Flock names a run without a results file by its tracker source, `run:entity/project/id`:
	// the page says it with the run's name.
	const refusal = $derived(
		data.refusal
			?.replaceAll(`run:${pathOf(data.a)}`, `${nameOf(data.a)}`)
			.replaceAll(`run:${data.b ? pathOf(data.b) : ''}`, `${data.b ? nameOf(data.b) : ''}`)
			.replace(/ has no results\.json$/, ' has no eval results (no results.json)')
	);
</script>

<svelte:window {onkeydown} />

<svelte:head>
	<title>Compare items · {nameOf(data.a)}</title>
</svelte:head>

{#snippet side(label: string, run: RunDetail | null, model: string | undefined)}
	{#snippet field()}
		{#if run}
			<span class="block truncate text-[15px]">{nameOf(run)}</span>
			<span class="block truncate font-mono text-[12px] text-muted-foreground" title={model}>
				{pathOf(run)}{model ? ` · ${model}` : ''}
			</span>
		{:else}
			<span class="block truncate text-[15px] text-muted-foreground">Pick a run to compare</span>
			<span class="block truncate text-[12px] text-faint">An eval run of any project</span>
		{/if}
	{/snippet}
	{#if label === 'A' && run}
		<EvalRunPicker
			side="A"
			entity={run.entity}
			project={run.project}
			run={run.id}
			bind:open={pickingA}
			onpick={(e, p, r) => show([e, p, r], data.b ? pathOf(data.b) : '')}
		>
			{@render field()}
		</EvalRunPicker>
	{:else}
		<!-- No B yet: its list starts in A's project. -->
		<EvalRunPicker
			side="B"
			entity={run?.entity ?? data.a.entity}
			project={run?.project ?? data.a.project}
			run={run?.id ?? ''}
			bind:open={pickingB}
			onpick={(e, p, r) => show(data.a, `${e}/${p}/${r}`)}
		>
			{@render field()}
		</EvalRunPicker>
	{/if}
{/snippet}

<!-- Without text (a run before schema 11), a cell's doc ids, where the diff lists them. -->
{#snippet ids(task: string, keep: ItemKeep)}
	{@const t = data.diff?.tasks.find((d) => d.task === task)}
	{#if t && (keep === 'only_a' || keep === 'only_b')}
		{@const list = t[keep]}
		<div class="min-w-0 px-3">
			<p class="mb-1 flex items-center gap-1 text-xs text-muted-foreground">
				Doc ids ({list.length})
				{#if list.length}<CopyButton text={list.join('\n')} label="Copy ids" class="size-6" />{/if}
			</p>
			<p class="max-h-40 overflow-y-auto font-mono text-[12px] break-all">
				{list.length ? list.join(' ') : '–'}
			</p>
		</div>
	{/if}
{/snippet}

<!-- The task column takes what the five counts leave, in both tables, so they line up. -->
{#snippet columns()}
	<colgroup>
		<col />
		<col class="w-[6.5rem]" />
		<col class="w-[7.5rem]" />
		<col class="w-[7.5rem]" />
		<col class="w-[7.5rem]" />
		<col class="w-[7.5rem]" />
	</colgroup>
{/snippet}

<Page width="home">
	<PageHeader title="Compare items" layout="home">
		{#snippet crumbs()}
			<Breadcrumbs
				crumbs={[
					{ label: data.a.project, href: projectHref(data.a.entity, data.a.project) },
					{
						label: 'Compare items',
						href: resolve('/(app)/[entity]/[project]/runs/[run]/compare', {
							entity: data.a.entity,
							project: data.a.project,
							run: data.a.id
						})
					}
				]}
			/>
		{/snippet}
	</PageHeader>

	<div class="grid gap-6 pb-16 text-[13px]">
		<div class="grid gap-2 sm:grid-cols-2">
			{@render side('A', data.a, data.diff?.a)}
			{@render side('B', data.b, data.diff?.b)}
		</div>

		{#if refusal}
			<p class="bg-card px-3 py-2.5" role="alert">
				These runs can’t be compared doc by doc: {refusal}
			</p>
		{:else if data.diff && data.b}
			<!-- A pair with no task in common has nothing to list: the reasons below say why. -->
			{#if tasks.length}
				<section aria-labelledby="items">
					<h2 id="items" class="mb-2 text-[22px] tracking-[-0.01em]">Items</h2>
					<!-- Another pair starts the items over. -->
					{#key `${pathOf(data.a)} ${pathOf(data.b)}`}
						<EvalItems
							entity={data.a.entity}
							project={data.a.project}
							run={data.a.id}
							other={pathOf(data.b)}
							sides={['A', 'B']}
							{tasks}
							keeps={[
								{ keep: 'only_a', label: 'Only A right' },
								{ keep: 'only_b', label: 'Only B right' },
								{ keep: 'both_wrong', label: 'Both wrong' },
								{ keep: 'both_right', label: 'Both right' }
							]}
							fallback={ids}
							bleed
						/>
					{/key}
				</section>
			{/if}

			<section aria-labelledby="compared">
				<h2 id="compared" class="mb-2 flex items-center gap-2 text-[22px] tracking-[-0.01em]">
					{data.diff.tasks.length} tasks compared
					<InfoHint label="About these counts">
						Doc ids are the dataset’s own ids, or a hash of the doc where it has none. Counts only:
						no test of significance.
					</InfoHint>
				</h2>
				<div class="overflow-x-auto">
					<table class="w-full min-w-[48rem] table-fixed border-t border-divider">
						{@render columns()}
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
								<tr class="border-b border-divider hover:bg-muted">
									<td class="truncate px-3 py-1.5">
										<span class="block truncate font-mono text-[12.5px]">{t.task}</span>
										<span class="text-xs text-faint" title={t.task_hash}>
											{t.metric} · {t.task_hash.slice(0, 12)}
										</span>
									</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.num_docs}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.both_right}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.both_wrong}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.only_a.length}</td>
									<td class="px-3 py-1.5 text-right tabular-nums">{t.only_b.length}</td>
								</tr>
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
			</section>

			{#if data.diff.not_compared.length}
				<section aria-labelledby="not-compared">
					<h2 id="not-compared" class="mb-2 text-[22px] tracking-[-0.01em]">
						{data.diff.not_compared.length} tasks not compared
					</h2>
					<div class="overflow-x-auto">
						<table class="w-full min-w-[48rem] table-fixed border-t border-divider">
							{@render columns()}
							<tbody>
								{#each data.diff.not_compared as [task, reason] (task)}
									<tr class="border-b border-divider">
										<td class="truncate px-3 py-1.5 font-mono text-[12.5px]" title={task}>{task}</td
										>
										<td colspan="5" class="px-3 py-1.5 text-muted-foreground"
											>{NOT_COMPARED[reason] ?? reason}</td
										>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</section>
			{/if}
		{/if}
	</div>
</Page>
