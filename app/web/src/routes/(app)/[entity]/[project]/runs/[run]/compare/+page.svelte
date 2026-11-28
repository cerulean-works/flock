<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import { SCROLL_FADE, scrollFade } from '#lib/scroll-fade.ts';
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ArrowUpDownIcon from '@lucide/svelte/icons/arrow-up-down';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { onMount, tick, untrack } from 'svelte';
	import { goto, replaceState } from '$app/navigation';

	import { page } from '$app/state';

	import CopyButton from '#lib/components/CopyButton.svelte';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import InfoHint from '#lib/components/InfoHint.svelte';
	import EvalItems from '#lib/components/run/EvalItems.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import RunPicker from '#lib/components/run/RunPicker.svelte';
	import BarSearch from '#lib/components/BarSearch.svelte';
	import ChartsPane from '#lib/components/ChartsPane.svelte';
	import FullscreenPanel from '#lib/components/FullscreenPanel.svelte';
	import PanelEditor from '#lib/components/PanelEditor.svelte';
	import PlotSettings from '#lib/components/PlotSettings.svelte';
	import CompareFacts from '#lib/components/compare/CompareFacts.svelte';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { pairWorkspace, type PairSide } from '#lib/data/run.ts';
	import { Workspace, setWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import {
		NOT_COMPARED,
		compareHref,
		formatScore,
		itemsViewOf,
		typedIn,
		withItemsView
	} from '#lib/evals.ts';
	import type { ItemDiff, ItemKeep, ItemsView, RunDetail, RunHistory } from '#lib/types.ts';
	import { runHref, withParam } from '#lib/workspace/url.ts';
	import { refusalSentence } from '#lib/eval-refusal.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// The pair's views: two eval runs open on their items, doc by doc; any other pair on its
	// charts. Summary, config and system are every pair's. In the URL, so a view can be linked.
	type Tab = 'items' | 'charts' | 'summary' | 'config' | 'system';
	const evalPair = $derived(data.compared != null);
	const TABS = $derived<{ value: Tab; label: string }[]>([
		...(evalPair ? [{ value: 'items' as const, label: 'Items' }] : []),
		{ value: 'charts', label: 'Charts' },
		{ value: 'summary', label: 'Summary' },
		{ value: 'config', label: 'Config' },
		{ value: 'system', label: 'System' }
	]);
	const defaultTab = $derived<Tab>(evalPair ? 'items' : 'charts');
	let tabOverride = $state<Tab | null>(null);
	const tab = $derived<Tab>(
		tabOverride ??
			TABS.find((t) => t.value === page.url.searchParams.get('tab'))?.value ??
			defaultTab
	);
	function setTab(t: Tab) {
		if (t === tab) return;
		tabOverride = t;
		ws.cursor = null;

		void goto(withParam(page.url, 'tab', t === defaultTab ? null : t), {
			replace: true,
			reset: false
		}).finally(() => (tabOverride = null));
	}
	// Summary and Config narrow their keys from the view bar.
	let keyQuery = $state('');

	// Both runs' curves stream in after the page: until then the charts wait.
	const NONE: RunHistory = { x: '_step', series: {} };
	let curves = $state<{ a: Omit<PairSide, 'run'>; b: Omit<PairSide, 'run'> } | null>(null);
	$effect(() => {
		const pending = data.curves;
		curves = null;
		pending?.then(
			(found) => {
				if (data.curves === pending) curves = found;
			},
			() => {
				if (data.curves === pending)
					curves = { a: { history: NONE, system: NONE }, b: { history: NONE, system: NONE } };
			}
		);
	});
	const now = Date.now() / 1000;
	// The two runs as a workspace of two, so the charts are the run page's: sections by prefix,
	// the panel search, full screen, and the plot settings with the x axis.
	const pair = $derived.by(() => {
		const a: PairSide = { run: data.a, ...(curves?.a ?? { history: NONE, system: NONE }) };
		const b: PairSide = data.b
			? { run: data.b, ...(curves?.b ?? { history: NONE, system: NONE }) }
			: a;
		return pairWorkspace(a, b, now);
	});
	const ws = new Workspace(
		() => pair.data.meta,
		() => pair.data.page
	);
	setWorkspace(ws);
	// The comparisons' own plot settings (the x axis, smoothing), kept from one pair to the next.
	$effect(() => {
		void [ws.meta.entity, ws.meta.project];
		untrack(() => ws.restoreLayout({ hidden: false }));
	});
	const charted = $derived(ws.sections.some((s) => s.panels.length > 0));

	// Full-screen panels, as on a run's page: `?panel=<panel id>`.
	const fullscreen = $derived.by(() => {
		const id = page.url.searchParams.get('panel');
		const i = id ? ws.findPanel(id) : -1;
		return i === -1 || tab !== 'charts' ? null : i;
	});
	const panelHref = (p: number) => withParam(page.url, 'panel', ws.panelDefs[p].id);
	const neighbours = $derived.by(() => {
		if (fullscreen == null) return { prev: null, next: null };
		const flat = ws.sections.flatMap((s) => s.panels);
		const i = flat.indexOf(fullscreen);
		return { prev: i > 0 ? flat[i - 1] : null, next: i < flat.length - 1 ? flat[i + 1] : null };
	});
	function openPanel(m: number, e?: MouseEvent) {
		if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0)) return;
		e?.preventDefault();
		ws.cursor = null;

		void goto(panelHref(m), { reset: false });
	}
	function closePanel(e?: MouseEvent) {
		e?.preventDefault();
		ws.cursor = null;
		ws.hover = null;

		void goto(withParam(page.url, 'panel', null), { replace: true, reset: false });
	}

	// The diff streams in after the page (031 D209), so the runs and a linked task's items show
	// first: until it lands there is no diff and no refusal.
	let diff = $state<ItemDiff | null>(null);
	let refusalText = $state<string | null>(null);
	let failure = $state<string | null>(null);
	$effect(() => {
		const pending = data.compared;
		// Another pair starts from its own link.
		pickedView = null;
		diff = null;
		refusalText = null;
		failure = null;
		// An answer for a pair the reader has since left is dropped.
		pending?.then(
			(found) => {
				if (data.compared !== pending) return;
				diff = found.diff;
				refusalText = found.refusal;
			},
			(cause: { message?: string }) => {
				if (data.compared === pending) failure = cause?.message ?? 'Flock gave no reason';
			}
		);
	});

	// A task the link names loads its items at once: a whole bank's diff takes seconds, one task's
	// items under one.
	const linked = $derived(itemsViewOf(page.url.searchParams).task);
	// The items' filters: each task with its four cells' counts.
	const tasks = $derived.by(() => {
		if (diff)
			return diff.tasks.map((t) => ({
				task: t.task,
				counts: {
					only_a: t.only_a.length,
					only_b: t.only_b.length,
					both_wrong: t.both_wrong,
					both_right: t.both_right
				}
			}));
		// Until then a task the link names is offered alone, without counts.
		return linked ? [{ task: linked, counts: {} }] : [];
	});
	// A linked task the diff leaves out restarts the items on a task it compared.
	const dropped = $derived(
		diff !== null && linked !== undefined && !diff.tasks.some((t) => t.task === linked)
	);

	const totals = $derived.by(() => {
		const sum = { docs: 0, both_right: 0, both_wrong: 0, only_a: 0, only_b: 0 };
		for (const t of diff?.tasks ?? []) {
			sum.docs += t.num_docs;
			sum.both_right += t.both_right;
			sum.both_wrong += t.both_wrong;
			sum.only_a += t.only_a.length;
			sum.only_b += t.only_b.length;
		}
		return sum;
	});

	// Each run's accuracy over the docs both scored: right in both, plus right in that run alone.
	const score = (bothRight: number, onlyA: number, onlyB: number, docs: number) => ({
		a: docs ? (bothRight + onlyA) / docs : NaN,
		b: docs ? (bothRight + onlyB) / docs : NaN
	});

	// The counts table's order: a column and a direction, the diff's own order until a column is
	// picked. A column's header turns it, as the runs table's do: the same column flips the
	// direction, a new one starts largest first (a task's name A to Z).
	type Column =
		'task' | 'docs' | 'a' | 'b' | 'lead' | 'both_right' | 'both_wrong' | 'only_a' | 'only_b';
	const COLUMNS: { key: Column; label: string }[] = [
		{ key: 'task', label: 'Task' },
		{ key: 'docs', label: 'Docs' },
		{ key: 'a', label: 'A' },
		{ key: 'b', label: 'B' },
		{ key: 'lead', label: 'Δ points' },
		{ key: 'both_right', label: 'Both right' },
		{ key: 'both_wrong', label: 'Both wrong' },
		{ key: 'only_a', label: 'Only A right' },
		{ key: 'only_b', label: 'Only B right' }
	];
	let order = $state<{ by: Column; desc: boolean } | null>(null);
	const compared = $derived.by(() => {
		const tasks = [...(diff?.tasks ?? [])];
		if (!order) return tasks;
		const { by, desc } = order;
		const value = (t: (typeof tasks)[number]): number => {
			const { a, b } = score(t.both_right, t.only_a.length, t.only_b.length, t.num_docs);
			if (by === 'docs') return t.num_docs;
			if (by === 'a') return a;
			if (by === 'b') return b;
			if (by === 'lead') return a - b;
			if (by === 'both_right') return t.both_right;
			if (by === 'both_wrong') return t.both_wrong;
			return by === 'only_a' ? t.only_a.length : t.only_b.length;
		};
		tasks.sort((x, y) => {
			// A task with no rate (no docs) goes last either way.
			const [vx, vy] = by === 'task' ? [0, 0] : [value(x), value(y)];
			if (Number.isNaN(vx) !== Number.isNaN(vy)) return Number.isNaN(vx) ? 1 : -1;
			const gap = by === 'task' ? x.task.localeCompare(y.task) : vx - vy;
			return desc ? -gap : gap;
		});
		return tasks;
	});
	function sortBy(by: Column) {
		order = order?.by === by ? { by, desc: !order.desc } : { by, desc: by !== 'task' };
	}

	// The lead's tint at full strength: the table's largest lead either way, as the items tint
	// their margins by the page's largest.
	const widestLead = $derived(
		Math.max(
			1e-9,
			...(diff?.tasks ?? []).map((t) => {
				const { a, b } = score(t.both_right, t.only_a.length, t.only_b.length, t.num_docs);
				return Number.isFinite(a - b) ? Math.abs(a - b) : 0;
			})
		)
	);

	// Clicking a task in the counts table opens its items above: the link takes the task (the
	// reader's group and order kept, or the first group with docs) and the list starts over there.
	let picked = $state(0);
	// The view a picked task opens on, handed to the list as it remounts: `page.url` doesn't
	// follow `replaceState` at once, so reading the link back would show the old task.
	let pickedView = $state<ItemsView | null>(null);
	function showTask(task: string) {
		const view = itemsViewOf(page.url.searchParams);
		const counts: Partial<Record<ItemKeep, number>> =
			tasks.find((t) => t.task === task)?.counts ?? {};
		const keeps = ['only_a', 'only_b', 'both_wrong', 'both_right'] as const;
		const keep =
			view.keep && (counts[view.keep] ?? 0) > 0
				? view.keep
				: keeps.find((k) => (counts[k] ?? 0) > 0);
		pickedView = { task, keep, sort: view.sort };
		replaceState(withItemsView(page.url, pickedView), page.state);
		picked += 1;
		// The reader goes where the task opened, focus included, so Tab and a screen reader follow.
		void tick().then(() => document.getElementById('items')?.focus());
	}

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
		// A select's own keys (the task select's typeahead) aren't the page's.
		if (typedIn(e.target)) return;
		if (pickingA || pickingB) return;
		const key = e.key.toLowerCase();
		if (key === 'a') pickingA = true;
		else if (key === 'b') pickingB = true;
		else return;
		e.preventDefault();
	}

	// Flock's reason, in one plain sentence naming the runs; the reason itself stays in a hint.
	const refusal = $derived(
		refusalText &&
			refusalSentence(refusalText, {
				[pathOf(data.a)]: nameOf(data.a),
				...(data.b ? { [pathOf(data.b)]: nameOf(data.b) } : {})
			})
	);
</script>

<svelte:window {onkeydown} />

<svelte:head>
	<title>Compare · {nameOf(data.a)}{data.b ? ` vs ${nameOf(data.b)}` : ''}</title>
</svelte:head>

{#snippet side(label: string, run: RunDetail | null, model: string | undefined)}
	{#snippet field()}
		{#if run}
			<span {@attach hint(`${pathOf(run)}${model ? `\n${model}` : ''}`)}>{nameOf(run)}</span>
		{:else}
			<span class="text-muted-foreground">Pick a run</span>
		{/if}
	{/snippet}
	{#if label === 'A' && run}
		<RunPicker
			side="A"
			entity={run.entity}
			project={run.project}
			run={run.id}
			bind:open={pickingA}
			exclude={data.b ? pathOf(data.b) : undefined}
			onpick={(e, p, r) => show([e, p, r], data.b ? pathOf(data.b) : '')}
		>
			{@render field()}
		</RunPicker>
	{:else}
		<!-- No B yet: its list starts in A's project, and reaches the org's as the search grows. -->
		<RunPicker
			side="B"
			entity={run?.entity ?? data.a.entity}
			project={run?.project ?? data.a.project}
			run={run?.id ?? ''}
			bind:open={pickingB}
			exclude={pathOf(data.a)}
			onpick={(e, p, r) => show(data.a, `${e}/${p}/${r}`)}
		>
			{@render field()}
		</RunPicker>
	{/if}
{/snippet}

<!-- Each run's accuracy over the docs compared, and A's lead in points. -->
{#snippet rates({ a, b }: { a: number; b: number })}
	<td class="px-3 py-1.5 text-right tabular-nums">{formatScore(a, 'acc')}</td>
	<td class="px-3 py-1.5 text-right tabular-nums">{formatScore(b, 'acc')}</td>
	<td class="px-3 py-1.5 text-right tabular-nums">
		{#if Number.isFinite(a - b)}
			<!-- A tint per sign, as strong as the lead is wide in the table: the items' margins'. -->
			<span
				class="rounded px-1"
				style:background-color="color-mix(in srgb, var({a >= b
					? '--state-finished'
					: '--destructive'}) {Math.round(
					(35 * Math.min(Math.abs(a - b), widestLead)) / widestLead
				)}%, transparent)"
				>{a > b ? '+' : a < b ? '−' : ''}{Math.abs(100 * (a - b)).toFixed(1)}</span
			>
		{:else}
			–
		{/if}
	</td>
{/snippet}

<!-- Without text (a run before schema 11), a cell's doc ids, where the diff lists them. -->
{#snippet ids(task: string, keep: ItemKeep)}
	{@const t = diff?.tasks.find((d) => d.task === task)}
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

<!-- The task column takes what the numbers leave, in both tables, so they line up. -->
{#snippet columns()}
	<colgroup>
		<col />
		<col class="w-[5rem]" />
		<col class="w-[5.5rem]" />
		<col class="w-[5.5rem]" />
		<col class="w-[6.5rem]" />
		<col class="w-[7.5rem]" />
		<col class="w-[7.5rem]" />
		<col class="w-[8.5rem]" />
		<col class="w-[8.5rem]" />
	</colgroup>
{/snippet}

<Page width="full">
	<PageHeader
		title="Compare"
		trail={[{ label: nameOf(data.a), href: runHref(data.a.entity, data.a.project, data.a.id) }]}
	>
		<!-- One row, as Linear's view bar: the pair as two quiet pickers, the views, then the
		     view's own controls at the right. -->
		{#snippet toolbar()}
			<div class="flex min-w-0 flex-1 items-center gap-1">
				<!-- Each side a share of the bar: a long name for A never squeezes B out. -->
				<span class="flex max-w-[16rem] min-w-0">{@render side('A', data.a, diff?.a)}</span>
				<span class="flex-none text-[13px] text-muted-foreground">vs</span>
				<span class="flex max-w-[16rem] min-w-0">{@render side('B', data.b, diff?.b)}</span>
				{#if data.b}
					<span class="mx-2 h-4 w-px flex-none bg-divider max-sm:hidden" aria-hidden="true"></span>
					<Tabs.Root value={tab} onValueChange={(v) => setTab(v as Tab)} class="min-w-0">
						<Tabs.List aria-label="Comparison">
							{#each TABS as t (t.value)}
								<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
							{/each}
						</Tabs.List>
					</Tabs.Root>
					<div class="ml-auto flex flex-none items-center gap-0.5">
						{#if tab === 'charts'}
							<PlotSettings />
						{:else if tab === 'summary' || tab === 'config'}
							<BarSearch bind:value={keyQuery} things="keys" />
						{/if}
					</div>
				{/if}
			</div>
		{/snippet}
	</PageHeader>

	<!-- One column no wider than the page: the counts tables (60rem at least) scroll in their own
	     boxes, and would otherwise widen the column, every section with it, so the items table's
	     bleed lands off centre and the page is cropped on the right. -->
	{#if data.b && tab === 'charts'}
		<!-- The charts fill the page and scroll themselves, as on a run's page. -->
		<main class="flex min-h-0 flex-1 flex-col">
			<!-- The pane places itself in this box, which takes what the foot leaves. -->
			<div class="relative min-h-0 flex-1">
				{#if !curves}
					<p
						class="flex items-center gap-2 px-5 pt-5 text-[13px] text-muted-foreground"
						role="status"
					>
						<LoaderCircleIcon class="size-4 animate-spin" aria-hidden="true" />
						Reading both runs' curves…
					</p>
				{:else if charted}
					<ChartsPane
						shown
						active={fullscreen == null}
						beside={false}
						pack={false}
						{panelHref}
						onfullscreen={openPanel}
					/>
				{:else}
					<EmptyState variant="compact">No metric was logged by both runs.</EmptyState>
				{/if}
			</div>
			{#if curves && (pair.only.a.length || pair.only.b.length)}
				<!-- What only one run logged, named at the foot rather than charted alone. -->
				<p
					class="flex flex-none gap-4 border-t border-divider px-5 py-2 text-xs text-muted-foreground"
				>
					{#each [['A', pair.only.a], ['B', pair.only.b]] as const as [label, keys] (label)}
						{#if keys.length}
							<span {@attach hint(keys.join('\n'))}>
								Only {label} logged {keys.length === 1 ? keys[0] : `${keys.length} metrics`}
							</span>
						{/if}
					{/each}
				</p>
			{/if}
		</main>
	{:else if data.b && tab !== 'items'}
		<!-- On the page's 20px gutters, which the tables bleed into. -->
		<main class="@container min-h-0 flex-1 overflow-y-auto px-5 pt-3 pb-16">
			<CompareFacts
				view={tab as 'summary' | 'config' | 'system'}
				a={data.a}
				b={data.b}
				query={keyQuery}
			/>
		</main>
	{:else}
		<main class="@container min-h-0 flex-1 overflow-y-auto">
			<div class="grid grid-cols-[minmax(0,1fr)] gap-6 px-5 pt-5 pb-16 text-[13px]">
				{#if failure}
					<p class="bg-card px-3 py-2.5" role="alert">The comparison failed: {failure}</p>
				{:else if refusal}
					<p class="bg-card px-3 py-2.5" role="alert" {@attach hint(refusalText ?? undefined)}>
						{refusal}
					</p>
				{:else if !data.b}
					<!-- Only A so far: B's list may be closed without a pick. -->
					<EmptyState variant="onboarding" title="Pick a run to compare">
						{#snippet actions()}
							<Button variant="quiet" size="bar" onclick={() => (pickingB = true)}
								>Pick run B</Button
							>
						{/snippet}
					</EmptyState>
				{:else}
					<!-- A pair with no task in common has nothing to list: the reasons below say why. -->
					{#if tasks.length}
						<section aria-labelledby="items">
							<h2
								id="items"
								tabindex="-1"
								class="mb-2 scroll-mt-4 rounded-md text-[15px] font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
							>
								Items
							</h2>
							<!-- Another pair, a task picked in the table below, or a linked task the diff left out
						starts the items over. -->
							{#key `${pathOf(data.a)} ${pathOf(data.b)} ${picked} ${dropped}`}
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
									view={pickedView ?? itemsViewOf(page.url.searchParams)}
									onview={(view) => replaceState(withItemsView(page.url, view), page.state)}
								/>
							{/key}
						</section>
					{/if}

					{#if !diff}
						<p class="flex items-center gap-2 text-muted-foreground" role="status">
							<LoaderCircleIcon class="size-4 animate-spin" aria-hidden="true" />
							Comparing every task of the two runs…
						</p>
					{:else}
						<section aria-labelledby="compared">
							<h2 id="compared" class="mb-2 flex items-center gap-2 text-[15px] font-medium">
								{diff.tasks.length} tasks compared
								<InfoHint label="About these counts">
									A and B are each run’s accuracy on the docs compared, and Δ points is A’s lead.
									Click a task to read its items above. Doc ids are the dataset’s own ids, or a hash
									of the doc where it has none. Counts only: no test of significance.
								</InfoHint>
							</h2>
							<div class={['relative bleed overflow-x-auto', SCROLL_FADE]} {@attach scrollFade}>
								<table
									class="w-full min-w-[66rem] table-fixed border-t border-divider [&_tr>*:first-child]:pl-5 [&_tr>*:last-child]:pr-5"
								>
									{@render columns()}
									<thead class="border-b text-xs text-muted-foreground">
										<tr>
											{#each COLUMNS as c (c.key)}
												{@const active = order?.by === c.key}
												<th
													class={[
														'px-3 py-2 font-medium whitespace-nowrap',
														c.key === 'task' ? 'text-left' : 'text-right'
													]}
													aria-sort={active
														? order?.desc
															? 'descending'
															: 'ascending'
														: undefined}
												>
													<!-- As the Slurm dashboard's headers: a faint ⇅ under the pointer, the direction once picked. -->
													<button
														type="button"
														class="group/sort inline-flex items-center gap-1 hover:text-foreground"
														onclick={() => sortBy(c.key)}
													>
														{c.label}
														{#if active && order?.desc}<ArrowDownIcon
																class="size-3 text-foreground"
															/>{:else if active}<ArrowUpIcon
																class="size-3 text-foreground"
															/>{:else}<ArrowUpDownIcon
																class="size-3 opacity-0 group-hover/sort:opacity-50"
															/>{/if}
													</button>
												</th>
											{/each}
										</tr>
									</thead>
									<tbody>
										{#each compared as t (t.task)}
											{@const rate = score(
												t.both_right,
												t.only_a.length,
												t.only_b.length,
												t.num_docs
											)}
											<!-- The row opens the task's items above; its name is the button for the keys. -->
											<tr
												class="h-9 cursor-pointer border-b border-divider hover:bg-muted"
												onclick={() => showTask(t.task)}
											>
												<td class="truncate px-3 py-1.5">
													<!-- The metric and hash in the hint: one line a task. -->
													<button
														type="button"
														class="block max-w-full truncate hover:underline"
														{@attach hint(
															`Show its items\n${t.metric} · ${t.task_hash.slice(0, 12)}`
														)}
													>
														{t.task}
													</button>
												</td>
												<td class="px-3 py-1.5 text-right tabular-nums">{t.num_docs}</td>
												{@render rates(rate)}
												<td class="px-3 py-1.5 text-right tabular-nums">{t.both_right}</td>
												<td class="px-3 py-1.5 text-right tabular-nums">{t.both_wrong}</td>
												<td class="px-3 py-1.5 text-right tabular-nums">{t.only_a.length}</td>
												<td class="px-3 py-1.5 text-right tabular-nums">{t.only_b.length}</td>
											</tr>
										{/each}
									</tbody>
									{#if diff.tasks.length > 1}
										<tfoot class="text-muted-foreground">
											<tr>
												<td class="px-3 py-2">All compared tasks</td>
												<td class="px-3 py-2 text-right tabular-nums">{totals.docs}</td>
												{@render rates(
													score(totals.both_right, totals.only_a, totals.only_b, totals.docs)
												)}
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

						{#if diff.not_compared.length}
							<section aria-labelledby="not-compared">
								<h2 id="not-compared" class="mb-2 text-[15px] font-medium">
									{diff.not_compared.length} tasks not compared
								</h2>
								<div class={['relative bleed overflow-x-auto', SCROLL_FADE]} {@attach scrollFade}>
									<table
										class="w-full min-w-[66rem] table-fixed border-t border-divider [&_tr>*:first-child]:pl-5 [&_tr>*:last-child]:pr-5"
									>
										{@render columns()}
										<tbody>
											{#each diff.not_compared as [task, reason] (task)}
												<tr class="border-b border-divider">
													<td class="truncate px-3 py-1.5" {@attach hint(task)}>{task}</td>
													<td colspan="8" class="px-3 py-1.5 text-muted-foreground"
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
				{/if}
			</div>
		</main>
	{/if}
</Page>

{#if fullscreen != null}
	<FullscreenPanel
		panel={fullscreen}
		closeHref={withParam(page.url, 'panel', null)}
		prevHref={neighbours.prev != null ? panelHref(neighbours.prev) : null}
		nextHref={neighbours.next != null ? panelHref(neighbours.next) : null}
		onclose={closePanel}
	/>
{/if}

<PanelEditor />
