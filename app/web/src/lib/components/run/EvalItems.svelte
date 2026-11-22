<script lang="ts">
	import ArrowDownUpIcon from '@lucide/svelte/icons/arrow-down-up';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import XIcon from '@lucide/svelte/icons/x';
	import { untrack, type Snippet } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import InfoHint from '#lib/components/InfoHint.svelte';
	import ResizeHandle from '#lib/components/ResizeHandle.svelte';
	import ItemChoiceRow from './ItemChoiceRow.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import type { EvalItem, ItemKeep, ItemsPage } from '#lib/types.ts';
	import { ITEM_W, readWidth, saveWidth } from '#lib/widths.ts';

	/**
	 * One task's docs, one row each, a page at a time (027): the varying part of the question, and
	 * per run whether it was right (icon and word, not colour alone), its signed margin and its
	 * pick. Flock lists the most telling rows first (D198). A row opens to the whole question and
	 * every choice's share, probability and log-probability per run. A caller that changes the runs
	 * remounts it (`{#key}`).
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
		bleed = false
	}: Props = $props();

	const SIZE = 20;
	// It starts on the first keep and the first task with docs in it; the choice is the reader's
	// from then on, so only the first values are read.
	let keep = $state<ItemKeep>(untrack(() => keeps[0].keep));
	let task = $state(
		untrack(() => (tasks.find((t) => (t.counts[keep] ?? 0) > 0) ?? tasks[0])?.task ?? '')
	);
	let sort = $state<'margin' | 'doc'>('margin');
	let at = $state(1);
	let found = $state<ItemsPage | null>(null);
	// Kept while the next page loads, so the pager (and the focus on it) stays.
	let total = $state<number | null>(null);
	let refusal = $state<string | null>(null);
	let loading = $state(false);
	const open = new SvelteSet<string>();
	// Rows opened on another page or before a re-sort don't fade this page's rows.
	const opened = $derived(found?.items.some((item) => open.has(item.id)) ?? false);

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

	const counts = $derived(tasks.find((t) => t.task === task)?.counts ?? {});
	const pages = $derived(Math.max(1, Math.ceil((total ?? 0) / SIZE)));

	// The page shown, read again whenever the task, the keep, the order or the page changes.
	$effect(() => {
		if (!task) return;
		const query = new URLSearchParams({
			task,
			keep,
			sort,
			offset: String((at - 1) * SIZE),
			limit: String(SIZE),
			...(other ? { with: other } : {})
		});
		const path = [entity, project, 'runs', run].map(encodeURIComponent).join('/');
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
				if (res.ok) {
					found = body as ItemsPage;
					total = found.total;
				} else {
					// Eval names a run by its `run:` source: the page names it as the columns do. The
					// longer path goes first, so a run named as a prefix of the other isn't matched in it.
					let message = (body as { message?: string } | null)?.message ?? `${res.status}`;
					const named = [`${entity}/${project}/${run}`, other].map((source, side) => ({
						source,
						side
					}));
					named.sort((x, y) => (y.source?.length ?? 0) - (x.source?.length ?? 0));
					for (const { source, side } of named)
						if (source) message = message.replaceAll(`run:${source}`, sides[side]);
					refusal = message;
				}
				loading = false;
			})
			.catch((err: Error) => {
				if (controller.signal.aborted) return;
				refusal = err.message;
				loading = false;
			});
		return () => controller.abort();
	});

	// The task's score per run and how many docs flipped, from the counts: a net gain can hide
	// much churn either way.
	const summary = $derived.by(() => {
		const { only_a = 0, only_b = 0, both_right = 0, both_wrong = 0, right = 0, all = 0 } = counts;
		if (other) {
			const docs = only_a + only_b + both_right + both_wrong;
			if (!docs) return null;
			const a = (both_right + only_a) / docs;
			const b = (both_right + only_b) / docs;
			const delta = 100 * (a - b);
			const sign = delta > 0 ? '+' : delta < 0 ? '−' : '±';
			return [
				`A ${a.toFixed(4)}, B ${b.toFixed(4)}`,
				`A ${sign}${Math.abs(delta).toFixed(1)} points`,
				`${only_a} docs only A got right, ${only_b} only B`
			].join(' · ');
		}
		if (!all) return null;
		return `${(right / all).toFixed(4)}: ${right} of ${all} docs right`;
	});

	// Each run's margin: its gold choice's score minus the best wrong one's, in the primary
	// metric's units (the log of a ratio of shares, as Flock sorts by). None for `acc_greedy`,
	// which ranks no choices, or a doc with one choice.
	const margins = $derived(
		new Map(
			(found?.items ?? []).map((item) => [
				item.id,
				item.runs.map(({ share }) => {
					const wrong = (share ?? []).filter((_, k) => k !== item.gold);
					if (!share || !wrong.length) return null;
					return Math.log(Math.max(share[item.gold], 1e-300) / Math.max(...wrong, 1e-300));
				})
			])
		)
	);
	// The tint's full strength: the page's largest margin.
	const widest = $derived(
		Math.max(1e-9, ...[...margins.values()].flat().map((m) => Math.abs(m ?? 0)))
	);

	// The whole lines every question of the page shares at both ends (a wrapper such as
	// `Question:` and `Answer:` lines), so a row shows what varies.
	const shared = $derived.by(() => {
		const questions = (found?.items ?? []).map((i) => i.question);
		if (questions.length < 2 || questions.some((q) => q === null)) return { head: '', tail: '' };
		const texts = questions as string[];
		// One pass over each question from either end: the characters all of them share.
		let start = texts[0].length;
		for (const q of texts) {
			let i = 0;
			while (i < start && q[i] === texts[0][i]) i++;
			start = i;
		}
		const head = texts[0].slice(0, start && texts[0].lastIndexOf('\n', start - 1) + 1);
		// The end is looked for after the start, so the two never overlap.
		const rests = texts.map((q) => q.slice(head.length));
		let end = rests[0].length;
		for (const r of rests) {
			let i = 0;
			while (i < end && i < r.length && r[r.length - 1 - i] === rests[0][rests[0].length - 1 - i])
				i++;
			end = i;
		}
		const common = rests[0].slice(rests[0].length - end);
		const tail = common.includes('\n') ? common.slice(common.indexOf('\n')) : '';
		// Questions that are all the same have nothing that varies: show them whole.
		if (rests.some((r) => !r.slice(0, r.length - tail.length).trim()))
			return { head: '', tail: '' };
		return { head, tail };
	});

	// A choice as a reader names it: Winogrande's choices share their ending, and the option
	// is in each choice's own context.
	const named = (item: EvalItem, k: number) => (item.contexts ?? item.choices)[k].trim();
	const signed = (m: number) => `${m > 0 ? '+' : m < 0 ? '−' : ''}${Math.abs(m).toFixed(2)}`;
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

<div class="grid gap-3">
	<div class="flex flex-wrap items-center gap-2">
		<Select.Root
			type="single"
			value={task}
			onValueChange={(v) => {
				task = v;
				at = 1;
				total = null;
				open.clear(); // doc ids repeat across tasks
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
						open.clear();
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
			onclick={() => {
				sort = sort === 'margin' ? 'doc' : 'margin';
				at = 1;
			}}
		>
			<ArrowDownUpIcon />
			{sort === 'margin' ? (other ? 'Biggest margin gap first' : 'Surest first') : 'Dataset order'}
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

	{#if refusal}
		<p class="bg-card px-3 py-2.5" role="alert">No item text for {task}: {refusal}</p>
		{@render fallback?.(task, keep)}
	{:else if found && !found.items.length}
		<p class="px-1 text-muted-foreground">No docs of {task} here.</p>
	{:else if found}
		{#if shared.head || shared.tail}
			<!-- Only the ends that exist: a shared start, a shared end, or both. -->
			<p class="truncate text-xs text-faint" title="{shared.head}…{shared.tail}">
				Rows leave out what every question here shares: <span class="font-mono"
					>{shared.head ? `${shared.head.slice(-40).trim()} …` : '…'}{shared.tail
						? ` ${shared.tail.slice(0, 40).trim()}`
						: ''}</span
				>
			</p>
		{/if}
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
					{#each found.items as item (item.id)}
						{@const shown = open.has(item.id)}
						{@const m = margins.get(item.id) ?? []}
						{@const q = item.question ?? ''}
						<!-- With a row open, the closed ones fade back so it reads on its own; hovering one brings it
						     forward again. -->
						<tr
							class={[
								'align-top transition-opacity hover:bg-muted/50',
								!shown && 'border-b border-divider',
								opened && !shown && 'opacity-40 focus-within:opacity-100 hover:opacity-100'
							]}
						>
							<td class="px-2 py-1.5">
								<button
									type="button"
									class="flex max-w-full items-center gap-1 font-mono text-[12px]"
									aria-expanded={shown}
									onclick={() => (shown ? open.delete(item.id) : open.add(item.id))}
								>
									<ChevronRightIcon
										class={['size-3.5 flex-none transition-transform', shown && 'rotate-90']}
									/>
									<span class="truncate" title={item.id}>{item.id}</span>
								</button>
							</td>
							<td class="px-2 py-1.5">
								<!-- Opened, the whole question as scored; closed, the part that varies. -->
								<span class={['whitespace-pre-wrap', !shown && 'line-clamp-2']}>
									{#if shown}{item.question ?? ''}{:else if item.question === null}{(
											item.contexts ?? item.choices
										)
											.map((c) => c.trim())
											.join(' / ')}{:else}{q
											.slice(shared.head.length, q.length - shared.tail.length)
											.trim()}{/if}
								</span>
							</td>
							<td class="px-2 py-1.5"><span class="line-clamp-2">{named(item, item.gold)}</span></td
							>
							{#each item.runs as answer, side (side)}
								{@const margin = m[side]}
								<td class="px-2 py-1.5">
									<!-- Right or wrong by the icon's shape, then what the run picked. -->
									<span class="flex items-start gap-1.5">
										{#if answer.correct}
											<CheckIcon class="mt-0.5 size-3.5 flex-none text-state-finished" />
										{:else}
											<XIcon class="mt-0.5 size-3.5 flex-none text-destructive" />
										{/if}
										<span class="sr-only">{answer.correct ? 'right:' : 'wrong:'}</span>
										<span class="line-clamp-2 min-w-0 flex-1"
											>{answer.pick === null ? '–' : named(item, answer.pick)}</span
										>
										{#if margin != null}
											<!-- A tint per sign, as strong as the margin is wide on this page. -->
											<span
												class="rounded px-1 font-mono text-[12px] tabular-nums"
												style:background-color="color-mix(in srgb, var({margin >= 0
													? '--state-finished'
													: '--destructive'}) {Math.round((35 * Math.abs(margin)) / widest)}%,
												transparent)">{signed(margin)}</span
											>
										{/if}
									</span>
								</td>
							{/each}
							{#if other}
								<td class="px-2 py-1.5 text-right font-mono text-[12px] tabular-nums">
									{m[0] != null && m[1] != null ? signed(m[0] - m[1]) : '–'}
								</td>
							{/if}
							<td></td>
						</tr>
						<!-- Opened: a row per choice in the same columns, so each run's numbers sit beside
						     the choice they're for. -->
						{#if shown}
							{#each item.choices, k (k)}
								<ItemChoiceRow {item} choice={k} {sides} delta={!!other} />
							{/each}
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
	{#if pages > 1}
		<nav class="flex items-center gap-2 text-[13px] tabular-nums" aria-label="Item pages">
			<Button
				variant="outline"
				size="sm"
				disabled={at <= 1}
				aria-label="Previous page"
				onclick={() => (at -= 1)}><ChevronLeftIcon /></Button
			>
			<span>
				{(at - 1) * SIZE + 1}–{Math.min(at * SIZE, total ?? 0)} of {total}
			</span>
			<Button
				variant="outline"
				size="sm"
				disabled={at >= pages}
				aria-label="Next page"
				onclick={() => (at += 1)}><ChevronRightIcon /></Button
			>
		</nav>
	{/if}
</div>
