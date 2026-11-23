<script lang="ts">
	import ArrowDownUpIcon from '@lucide/svelte/icons/arrow-down-up';
	import ArrowDownWideNarrowIcon from '@lucide/svelte/icons/arrow-down-wide-narrow';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import XIcon from '@lucide/svelte/icons/x';
	import { tick, untrack, type Snippet } from 'svelte';
	import InfoHint from '#lib/components/InfoHint.svelte';
	import PageNumbers from '#lib/components/PageNumbers.svelte';
	import ResizeHandle from '#lib/components/ResizeHandle.svelte';
	import ItemChoiceRow from './ItemChoiceRow.svelte';
	import ItemsMap from './ItemsMap.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { signedMargin, typedIn } from '#lib/evals.ts';
	import type { EvalItem, ItemKeep, ItemsPage, ItemSummary, ItemsView } from '#lib/types.ts';
	import { ITEM_W, readWidth, saveWidth } from '#lib/widths.ts';

	/**
	 * One task's docs, one row each, a page at a time (027): the varying part of the question, and
	 * per run whether it was right (icon and word, not colour alone), its signed margin and its
	 * pick. Flock lists the most telling rows first (D198). One row opens at a time, to the whole
	 * question and every choice's share, probability and log-probability per run, fetched then
	 * (028). j and k move through the docs, Enter opens, Esc closes. With two runs, a square scatter
	 * plot per suite sits above the list, and a click on a dot opens that doc here (029). A caller
	 * that changes the runs remounts it (`{#key}`).
	 */
	interface Props {
		/** The run read, A when there are two. */
		entity: string;
		project: string;
		run: string;
		/** Run B as `entity/project/id`, for two runs side by side. */
		other?: string;
		/** Each run's column heading, in order: `A` and `B`, or one run's. */
		sides: string[];
		/** The tasks offered, with how many docs each keep holds. */
		tasks: { task: string; counts: Partial<Record<ItemKeep, number>> }[];
		/** The keeps offered, the first by default. */
		keeps: { keep: ItemKeep; label: string }[];
		/** Under Flock's refusal (a run without text): what can be shown without text. */
		fallback?: Snippet<[string, ItemKeep]>;
		/** On a `home` page: the table's box spans the page, from under the text to its edges. */
		bleed?: boolean;
		/** Where a link left the list; a part that doesn't fit these tasks or keeps is ignored. */
		view?: ItemsView;
		/** Told the list's state whenever the reader changes it, so the page's link can follow. */
		onview?: (view: ItemsView) => void;
	}

	let {
		entity,
		project,
		run,
		other,
		sides,
		tasks,
		keeps,
		fallback,
		bleed = false,
		view = {},
		onview
	}: Props = $props();

	const SIZE = 20;
	// It starts where the link left it, else on the first keep and the first task with docs in it;
	// the choice is the reader's from then on, so only the first values are read.
	let keep = $state<ItemKeep>(
		untrack(() => (keeps.find((k) => k.keep === view.keep) ?? keeps[0]).keep)
	);
	let task = $state(
		untrack(
			() =>
				(
					tasks.find((t) => t.task === view.task) ??
					tasks.find((t) => (t.counts[keep] ?? 0) > 0) ??
					tasks[0]
				)?.task ?? ''
		)
	);
	let sort = $state<'margin' | 'doc'>(untrack(() => view.sort ?? 'margin'));
	// A page past what Flock reads (an offset of 2^31) is past the last: the last one shows.
	let at = $state(untrack(() => Math.min(view.page ?? 1, Math.floor(2 ** 31 / SIZE) + 1)));
	let found = $state<ItemsPage | null>(null);
	// Kept while the next page loads, so the pager (and the focus on it) stays.
	let total = $state<number | null>(null);
	let refusal = $state<string | null>(null);
	let loading = $state(false);
	// The open row's id, and its whole item once Flock sends it.
	let open = $state<string | null>(untrack(() => view.item ?? null));
	let detail = $state<EvalItem | null>(null);
	let detailRefusal = $state<string | null>(null);
	// Where j, k or a click on the map land once the page they turned to arrives, and whether they
	// open it. Only that page's answer lands there: any other change of view drops it.
	let landing: {
		page: string;
		row: (rows: ItemSummary[]) => ItemSummary | undefined;
		open: boolean;
	} | null = null;
	let root = $state<HTMLElement>();
	// An item open on another page or before a re-sort doesn't fade this page's rows.
	const opened = $derived(found?.items.some((item) => item.id === open) ?? false);
	const path = $derived([entity, project, 'runs', run].map(encodeURIComponent).join('/'));

	// Column widths the reader dragged, kept in cookies as the runs table's are. The items render
	// only in the browser (they're fetched there), so the cookies are read there too.
	type Column = keyof typeof ITEM_W;
	const widths = $state(
		Object.fromEntries(Object.entries(ITEM_W).map(([k, w]) => [k, w.initial])) as Record<
			Column,
			number
		>
	);
	$effect(() => {
		const cookies = new Map(
			document.cookie.split('; ').map((c) => c.split('=') as [string, string])
		);
		for (const [k, w] of Object.entries(ITEM_W))
			widths[k as Column] = readWidth(w, cookies.get(w.cookie));
	});
	const DELTA_W = 64;
	const tableWidth = $derived(
		widths.doc + widths.question + widths.gold + widths.run * sides.length + (other ? DELTA_W : 0)
	);
	// Doc, question, gold, a column per run, Δ with two, and the filler.
	const columns = $derived(4 + sides.length + (other ? 1 : 0));

	const counts = $derived(tasks.find((t) => t.task === task)?.counts ?? {});
	const pages = $derived(Math.max(1, Math.ceil((total ?? 0) / SIZE)));

	// Every change the reader makes ends here, so the page's link follows it. Untracked: the page
	// writes its URL, and an effect that read it back would loop (027 D195).
	function tell() {
		untrack(() =>
			onview?.({
				task,
				keep,
				sort: sort === 'doc' ? 'doc' : undefined,
				page: at > 1 ? at : undefined,
				item: open ?? undefined
			})
		);
	}

	// Flock's refusal in the page's words: eval names a run by its `run:` source, the page as the
	// columns do. The longer path goes first, so a run named as a prefix of the other isn't
	// matched in it.
	function refused(body: unknown, status: number): string {
		let message = (body as { message?: string } | null)?.message ?? `${status}`;
		const named = [`${entity}/${project}/${run}`, other].map((source, side) => ({ source, side }));
		named.sort((x, y) => (y.source?.length ?? 0) - (x.source?.length ?? 0));
		for (const { source, side } of named)
			if (source) message = message.replaceAll(`run:${source}`, sides[side]);
		return message;
	}

	// The page shown, read again whenever the task, the keep, the order or the page changes.
	const query = $derived(
		new URLSearchParams({
			task,
			keep,
			sort,
			offset: String((at - 1) * SIZE),
			limit: String(SIZE),
			...(other ? { with: other } : {})
		}).toString()
	);
	$effect(() => {
		if (!task) return;
		if (landing?.page !== query) landing = null;
		const controller = new AbortController();
		// Another choice's page or refusal never shows under this one.
		found = null;
		refusal = null;
		loading = true;
		fetch(`/api/projects/${path}/items?${query}`, { signal: controller.signal })
			.then(async (res) => {
				const body = await res.json().catch(() => null);
				// Aborted while the body was read: the next request owns the state.
				if (controller.signal.aborted) return;
				loading = false;
				if (!res.ok) {
					refusal = refused(body, res.status);
					return;
				}
				const page = body as ItemsPage;
				total = page.total;
				// A link past the last page (the docs changed since) shows the last one.
				if (!page.items.length && page.total && at > 1) {
					at = Math.ceil(page.total / SIZE);
					tell();
					return;
				}
				found = page;
				const to = landing?.row(page.items);
				if (!landing || !to) return;
				// j or k turned the page, on to its first or last doc, opened if one was; or the
				// map did, on to the doc clicked.
				if (landing.open) open = to.id;
				landing = null;
				tell();
				focus(to.id);
			})
			.catch((err: Error) => {
				if (controller.signal.aborted) return;
				refusal = err.message;
				loading = false;
			});
		return () => controller.abort();
	});

	// The open item's whole text and per-choice numbers, fetched when it opens.
	$effect(() => {
		if (open === null) return;
		// The id as a query value: any id, `..` or empty too, reaches Flock as written.
		const asked = new URLSearchParams({
			doc: open,
			task,
			keep,
			...(other ? { with: other } : {})
		});
		const controller = new AbortController();
		detail = null;
		detailRefusal = null;
		fetch(`/api/projects/${path}/item?${asked}`, { signal: controller.signal })
			.then(async (res) => {
				const body = await res.json().catch(() => null);
				if (controller.signal.aborted) return;
				if (res.ok) detail = body as EvalItem;
				else detailRefusal = refused(body, res.status);
			})
			.catch((err: Error) => {
				if (!controller.signal.aborted) detailRefusal = err.message;
			});
		return () => controller.abort();
	});

	function toggle(id: string) {
		open = open === id ? null : id;
		tell();
	}

	// A mark of the map: its cell's list in the default order, at the page that holds the doc,
	// with the doc open.
	function openAt(to: string, cell: ItemKeep, index: number, id: string) {
		const page = Math.floor(index / SIZE) + 1;
		const shown = task === to && keep === cell && sort === 'margin' && at === page && found;
		open = id;
		if (shown) {
			tell();
			focus(id);
			return;
		}
		if (task !== to || keep !== cell) total = null;
		task = to;
		keep = cell;
		sort = 'margin';
		at = page;
		landing = { page: query, row: (rows) => rows.find((r) => r.id === id), open: true };
		tell();
	}

	// After the rows render: the doc's toggle takes the focus, which scrolls it into view.
	function focus(id: string) {
		void tick().then(() =>
			root?.querySelector<HTMLElement>(`[data-item="${CSS.escape(id)}"]`)?.focus()
		);
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented || !found?.items.length) return;
		if (typedIn(e.target)) return;
		const rows = found.items;
		const key = e.key.toLowerCase();
		// From the open doc, else the one whose toggle has focus.
		const focused = (document.activeElement as HTMLElement | null)?.dataset.item;
		const i = rows.findIndex((r) => r.id === (open ?? focused));
		const step = key === 'j' ? 1 : key === 'k' ? -1 : 0;
		const to = i < 0 ? (step > 0 ? 0 : rows.length - 1) : i + step;
		if (key === 'escape' && open !== null) {
			focus(open);
			toggle(open);
		} else if (key === 'o' && i >= 0) {
			toggle(rows[i].id);
		} else if (step && to >= 0 && to < rows.length) {
			if (open !== null) toggle(rows[to].id);
			focus(rows[to].id);
		} else if (step && at + step >= 1 && at + step <= pages) {
			// Past the page's end: the next page's first doc, or the previous page's last.
			at += step;
			landing = {
				page: query,
				row: (rows) => rows[step > 0 ? 0 : rows.length - 1],
				open: open !== null
			};
			open = null;
			tell();
		} else {
			return; // not a key of the list's, or nowhere to go
		}
		e.preventDefault();
	}

	// One run's score on the task, from the counts.
	const summary = $derived.by(() => {
		const { right = 0, all = 0 } = counts;
		// Two runs' scores and flips are the counts table's, under the list (D202).
		if (other || !all) return null;
		return `${(right / all).toFixed(4)}: ${right} of ${all} docs right`;
	});

	// The tint's full strength: the page's largest margin.
	const widest = $derived(
		Math.max(
			1e-9,
			...(found?.items ?? []).flatMap((item) => item.runs.map((r) => Math.abs(r.margin ?? 0)))
		)
	);
</script>

<!-- A column heading with the handle that resizes its column. -->
<!-- The text truncates, not the cell, so the handle on its edge isn't clipped. -->
{#snippet head(text: string, column: Column | null)}
	<th class="relative px-2 py-1.5 text-left font-medium">
		<span class="block truncate">{text}</span>
		{#if column}
			<ResizeHandle
				value={widths[column]}
				bounds={ITEM_W[column]}
				label="Resize the {text} column"
				onresize={(px) => (widths[column] = px)}
				oncommit={(px) => saveWidth(ITEM_W[column], px)}
			/>
		{/if}
	</th>
{/snippet}

<svelte:window {onkeydown} />

<div class="grid gap-3" bind:this={root}>
	<div class="flex flex-wrap items-center gap-2">
		<Select.Root
			type="single"
			value={task}
			onValueChange={(v) => {
				task = v;
				at = 1;
				total = null;
				open = null; // doc ids repeat across tasks
				tell();
			}}
		>
			<Select.Trigger size="sm" class="max-w-80 min-w-56 font-mono text-[12.5px]" aria-label="Task">
				{task}
			</Select.Trigger>
			<Select.Content class="max-h-96">
				{#each tasks as t (t.task)}
					<Select.Item value={t.task} label={t.task}>
						<span class="min-w-0 flex-1 truncate font-mono text-[12.5px]">{t.task}</span>
						<span class="text-xs text-muted-foreground tabular-nums">{t.counts[keep] ?? ''}</span>
					</Select.Item>
				{/each}
			</Select.Content>
		</Select.Root>
		<div class="flex flex-wrap gap-1" role="radiogroup" aria-label="Docs shown">
			{#each keeps as k (k.keep)}
				<button
					type="button"
					role="radio"
					aria-checked={k.keep === keep}
					class="rounded-md border px-2.5 py-1 text-[13px] hover:bg-muted aria-checked:border-ring aria-checked:bg-accent"
					onclick={() => {
						if (k.keep === keep) return;
						keep = k.keep;
						at = 1;
						total = null;
						open = null;
						tell();
					}}
				>
					{k.label}
					<span class="ml-1 text-muted-foreground tabular-nums">{counts[k.keep] ?? ''}</span>
				</button>
			{/each}
		</div>
		<Button
			variant="ghost"
			size="sm"
			class="ml-auto text-muted-foreground"
			title={sort === 'margin'
				? other
					? 'The biggest gap between the runs’ margins first'
					: 'The surest first, right or wrong'
				: 'The dataset’s order'}
			onclick={() => {
				sort = sort === 'margin' ? 'doc' : 'margin';
				at = 1;
				open = null;
				tell();
			}}
		>
			{#if sort === 'margin'}<ArrowDownWideNarrowIcon />{:else}<ArrowDownUpIcon />{/if}
			{sort === 'margin' ? 'Margin' : 'Doc order'}
		</Button>
		<InfoHint label="About the items">
			A run’s margin is its gold choice’s score minus its best wrong choice’s, in the task’s primary
			metric (per character for <code class="font-mono">acc_per_char</code>): above zero it got the
			doc right, and the further from zero the surer it was (one that rounds to zero can show either
			sign: the scorer picked in FP32). Open a row for each choice’s share, probability and
			log-probability.
		</InfoHint>
	</div>
	{#if summary}
		<p class="text-xs text-muted-foreground tabular-nums">{summary}</p>
	{/if}

	{#if other}
		<ItemsMap {path} {other} {task} onopen={openAt} />
	{/if}

	{#if refusal}
		<p class="bg-card px-3 py-2.5" role="alert">No item text for {task}: {refusal}</p>
		{@render fallback?.(task, keep)}
	{:else if found && !found.items.length}
		<p class="px-1 text-muted-foreground">No docs of {task} here.</p>
	{:else if found}
		<!-- Columns as wide as the reader dragged them; a table wider than its box scrolls sideways.
		     With `bleed` the box spans the page (as the dataset rows do), padded so the table starts
		     under the text (Home's 70rem column), so a wide table grows to the page's edges first. -->
		<div class={['overflow-x-auto', bleed && 'bleed px-[max(0px,calc((100cqw-70rem)/2))]']}>
			<table
				class="min-w-full table-fixed border-t border-divider text-[13px]"
				style:width="{tableWidth}px"
			>
				<colgroup>
					<col style:width="{widths.doc}px" />
					<col style:width="{widths.question}px" />
					<col style:width="{widths.gold}px" />
					{#each sides as side (side)}<col style:width="{widths.run}px" />{/each}
					{#if other}<col style:width="{DELTA_W}px" />{/if}
					<!-- Takes whatever width the columns leave, so each renders at its dragged width. -->
					<col />
				</colgroup>
				<thead class="border-b text-xs text-muted-foreground">
					<tr>
						{@render head('Doc', 'doc')}
						{@render head('Question', 'question')}
						{@render head('Gold', 'gold')}
						<!-- The runs share one width: only the first has a handle, so an edge moves
						     with the pointer. -->
						{#each sides as side, i (side)}{@render head(side, i === 0 ? 'run' : null)}{/each}
						{#if other}<th class="px-2 py-1.5 text-right font-medium">Δ</th>{/if}
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each found.items as row (row.id)}
						{@const shown = open === row.id}
						<!-- The open row's whole item, once it has come. -->
						{@const item = shown && detail?.id === row.id ? detail : null}
						{@const m = row.runs.map((r) => r.margin)}
						<!-- With a row open, the closed ones fade back so it reads on its own; hovering one brings it
						     forward again. -->
						<tr
							class={[
								'align-top transition-opacity hover:bg-muted/50 has-[:focus-visible]:bg-muted/50',
								!shown && 'border-b border-divider',
								opened && !shown && 'opacity-40 focus-within:opacity-100 hover:opacity-100'
							]}
						>
							<td class="px-2 py-1.5">
								<button
									type="button"
									class="flex max-w-full items-center gap-1 font-mono text-[12px]"
									aria-expanded={shown}
									data-item={row.id}
									onclick={() => toggle(row.id)}
								>
									<ChevronRightIcon
										class={['size-3.5 flex-none transition-transform', shown && 'rotate-90']}
									/>
									<span class="truncate" title={row.id}>{row.id}</span>
								</button>
							</td>
							<td class="px-2 py-1.5">
								<!-- Opened, the whole question as scored; closed, the part that varies. -->
								<span class={['whitespace-pre-wrap', !item && 'line-clamp-2']}
									>{item ? (item.question ?? '') : row.text}</span
								>
							</td>
							<td class="px-2 py-1.5"><span class="line-clamp-2">{row.gold_text}</span></td>
							{#each row.runs as answer, side (side)}
								<td
									class="px-2 py-1.5"
									title="Gold choice’s probability {(100 * answer.gold_prob).toFixed(1)}%"
								>
									<!-- Right or wrong by the icon's shape, then what the run picked. -->
									<span class="flex items-start gap-1.5">
										{#if answer.correct}
											<CheckIcon class="mt-0.5 size-3.5 flex-none text-state-finished" />
										{:else}
											<XIcon class="mt-0.5 size-3.5 flex-none text-destructive" />
										{/if}
										<span class="sr-only">{answer.correct ? 'right:' : 'wrong:'}</span>
										<span class="line-clamp-2 min-w-0 flex-1">{answer.pick_text ?? '–'}</span>
										{#if answer.margin != null}
											<!-- A tint per sign, as strong as the margin is wide on this page. -->
											<span
												class="rounded px-1 font-mono text-[12px] tabular-nums"
												style:background-color="color-mix(in srgb, var({answer.margin >= 0
													? '--state-finished'
													: '--destructive'}) {Math.round(
													(35 * Math.abs(answer.margin)) / widest
												)}%, transparent)">{signedMargin(answer.margin)}</span
											>
										{/if}
									</span>
								</td>
							{/each}
							{#if other}
								<td class="px-2 py-1.5 text-right font-mono text-[12px] tabular-nums">
									{m[0] != null && m[1] != null ? signedMargin(m[0] - m[1]) : '–'}
								</td>
							{/if}
							<td></td>
						</tr>
						<!-- Opened: a row per choice in the same columns, so each run's numbers sit beside
						     the choice they're for. -->
						{#if item}
							{#each item.choices, k (k)}
								<ItemChoiceRow {item} choice={k} {sides} delta={!!other} />
							{/each}
						{:else if shown}
							<tr class="border-b border-divider">
								<td></td>
								<td colspan={columns - 1} class="px-2 py-1.5 text-muted-foreground">
									{#if detailRefusal}
										<span role="alert">No text for this doc: {detailRefusal}</span>
									{:else}
										<span class="flex items-center gap-2">
											<LoaderCircleIcon class="size-3.5 animate-spin" /> Loading the doc…
										</span>
									{/if}
								</td>
							</tr>
						{/if}
					{/each}
				</tbody>
			</table>
		</div>
	{:else if loading}
		<p class="flex items-center gap-2 text-muted-foreground">
			<LoaderCircleIcon class="size-3.5 animate-spin" /> Loading items…
		</p>
	{/if}
	<!-- Kept under a refused page, so the reader can go back to one that loads. -->
	{#if total}
		<PageNumbers
			current={at}
			{pages}
			first={(at - 1) * SIZE + 1}
			last={Math.min(at * SIZE, total)}
			{total}
			label="Item pages"
			ongo={(p) => {
				at = p;
				open = null;
				tell();
			}}
		/>
	{/if}
	<!-- The keys, named as the pickers name theirs. -->
	{#if found?.items.length}
		<p class="flex gap-3 text-[11px] text-muted-foreground" aria-hidden="true">
			<span>j k move</span><span>↵ o open</span><span>esc close</span>
		</p>
	{/if}
</div>
