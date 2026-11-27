<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import type { Scope } from '#lib/scope.ts';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import PageNumbers from '#lib/components/PageNumbers.svelte';
	import { DialogSubject } from '#lib/data/dialog-subject.svelte.ts';
	import { cellCut, cellText, datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { Cell, DatasetInfo, FullCell, RowsPage } from '#lib/types.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import CellView from './CellView.svelte';
	import ImageDialog from './ImageDialog.svelte';
	import Skeleton from '#lib/components/Skeleton.svelte';

	interface Props {
		scope: Scope;
		path: string;
		/** The dataset at the version shown: every read asks for that version. */
		info: DatasetInfo;
		/** Columns the viewer hid (the Columns picker beside the tabs); they aren't read. */
		hidden: SvelteSet<string>;
	}

	let { scope, path, info, hidden }: Props = $props();

	// Pages by offset, kept in the URL (D90): 50 rows, or 10 with binary columns.
	// Rows to a page: fewer for rows of images and audio. The pager's menu changes it.
	const sizes = $derived(info.binary ? [10, 25, 50] : [25, 50, 100]);
	let picked = $state<number | null>(null);
	const size = $derived(picked ?? (info.binary ? 10 : 50));
	const offset = $derived(
		Math.max(0, Math.floor(Number(page.url.searchParams.get('offset')) || 0))
	);

	const names = $derived(info.schema.map((f) => f.name));
	const shown = $derived(names.filter((n) => !hidden.has(n)));

	let rows = $state<RowsPage | null>(null);
	let failed = $state<string | null>(null);
	let loading = $state(false);

	$effect(() => {
		const query = {
			path,
			version: info.version,
			offset,
			limit: size,
			columns: hidden.size ? shown : null
		};
		if (!shown.length) {
			rows = null;
			return;
		}
		const controller = new AbortController();
		loading = true;
		failed = null;
		datasetCall<RowsPage>(datasetsApi(scope, 'rows', query), { signal: controller.signal })
			.then((got) => (rows = got))
			.catch((e: Error) => {
				if (!controller.signal.aborted) failed = e.message;
			})
			.finally(() => {
				if (!controller.signal.aborted) loading = false;
			});
		return () => controller.abort();
	});

	function go(to: number) {
		void goto(withParam(page.url, 'offset', to > 0 ? String(to) : null), { reset: false });
	}

	// Columns with long text or vectors wrap and share the width left over; the rest stay on one
	// line at their own width.
	const long = (cell: Cell | undefined) =>
		!!cell && (cell.k === 'vec' || ((cell.k === 'str' || cell.k === 'json') && cell.v.length > 40));
	const wraps = $derived(rows?.columns.map((_, j) => rows!.rows.some((row) => long(row[j]))) ?? []);
	const filled = $derived(wraps.some(Boolean));

	// When every column fits at that width, none is capped. When they don't, each column gets
	// at most CAP_SHORT (one that would sit on one line) or CAP_LONG (one that wraps), and the
	// table scrolls sideways.
	const CAP_SHORT = 320;
	const CAP_LONG = 448;
	const MIN_LONG = 256;
	// A header's type is cut short past this, so a long struct type can't widen its column.
	const TYPE_MAX = 192;
	// Cell padding, and a little more so text measured to fit doesn't wrap after rounding.
	const PAD = 20;
	let width = $state(0);
	let canvas: CanvasRenderingContext2D | null = null;
	function measure(text: string, font: string) {
		canvas ??= document.createElement('canvas').getContext('2d');
		if (!canvas) return text.length * 7;
		canvas.font = font;
		return canvas.measureText(text).width;
	}
	/** The widest line of a cell, as it shows on one line. */
	function cellWidth(cell: Cell, sans: string, mono: string) {
		if (cell.k === 'image') return 128;
		if (cell.k === 'vec') return CAP_LONG;
		const text =
			cell.k === 'str' || cell.k === 'json'
				? cell.v
				: cell.k === 'num' && typeof cell.v === 'number' && Number.isInteger(cell.v)
					? cellText({ ...cell, v: cell.v.toLocaleString('en-US') })
					: cellText(cell);
		const font = cell.k === 'json' ? `12px ${mono}` : `13px ${sans}`;
		return Math.max(0, ...text.split('\n', 8).map((line) => measure(line, font)));
	}
	let table = $state<HTMLElement | null>(null);
	/** Each column's width on one line, header included; null before the page is laid out. */
	const natural = $derived.by(() => {
		if (!rows || !width || !table) return null;
		const style = getComputedStyle(table);
		const sans = style.fontFamily;
		const mono = style.getPropertyValue('--font-mono') || 'monospace';
		return rows.columns.map((column, j) =>
			Math.max(
				measure(column.name, `500 12px ${sans}`) +
					Math.min(measure(column.type, `11px ${mono}`), TYPE_MAX) +
					4,
				...rows!.rows.map((row) => (row[j] ? cellWidth(row[j], sans, mono) : 0))
			)
		);
	});
	const capped = $derived.by(() => {
		if (!natural || !rows) return false;
		const numbers = measure(
			(rows.offset + rows.rows.length).toLocaleString('en-US'),
			'13px sans-serif'
		);
		const need = natural.reduce(
			(sum, w, j) => sum + (wraps[j] ? MIN_LONG : w) + PAD,
			numbers + PAD
		);
		return need > width;
	});
	/** A capped column's width, or null when columns take their own. */
	const capOf = (j: number) =>
		capped && natural ? Math.ceil(Math.min(natural[j], wraps[j] ? CAP_LONG : CAP_SHORT)) : null;

	const total = $derived(rows?.total ?? info.rows);
	const last = $derived(Math.min(offset + (rows?.rows.length ?? 0), total));
	const current = $derived(Math.floor(offset / size) + 1);
	const pages = $derived(Math.max(1, Math.ceil(total / size)));

	// Rows opened in place, by number. Their cut cells are read in full when they open, keyed
	// `row:column`, and kept for when the row opens again.
	const opened = new SvelteSet<number>();
	const full = new SvelteMap<string, Cell | Error>();

	const isCut = (cell: Cell) => cellCut(cell) || (cell.k === 'vec' && cell.head.length < cell.len);

	function toggle(row: number, cells: Cell[]) {
		if (opened.delete(row)) return;
		opened.add(row);
		rows?.columns.forEach((column, j) => {
			const cell = cells[j];
			const key = `${row}:${column.name}`;
			if (!cell || !isCut(cell) || full.has(key)) return;
			datasetCall<FullCell>(
				datasetsApi(scope, 'cell', { path, version: info.version, row, column: column.name })
			)
				.then((got) => full.set(key, cell.k === 'vec' ? asVector(cell, got.cell) : got.cell))
				.catch((e: Error) => full.set(key, e));
		});
	}

	/** The API sends a whole vector as JSON: back to a vector, with every value. */
	function asVector(cell: Extract<Cell, { k: 'vec' }>, got: Cell): Cell {
		if (got.k !== 'json' || got.len) return got;
		try {
			return { ...cell, head: JSON.parse(got.v) };
		} catch {
			return got;
		}
	}

	function onrowclick(e: MouseEvent, row: number, cells: Cell[]) {
		const target = e.target as Element;
		// Buttons act on their own, and selecting text doesn't open or close the row.
		if (target.closest('button, a')) return;
		const selection = getSelection();
		if (selection && !selection.isCollapsed && target.closest('tr')?.contains(selection.anchorNode))
			return;
		toggle(row, cells);
	}

	const image = new DialogSubject<{
		cell: Extract<Cell, { k: 'image' }>;
		row: number;
		column: string;
	}>();
</script>

<!-- The list tables' look (Gate 2): 12px muted labels, rows and rules to the page's edges. -->
<div class="grid gap-3">
	{#if !shown.length}
		<p class="px-5 pt-5 text-[13px] text-muted-foreground">Pick a column to show.</p>
	{:else if failed}
		<p class="px-5 pt-5 text-[13px] text-destructive" role="alert">{failed}</p>
	{:else if !rows}
		<div class="px-5 pt-5"><Skeleton count={10} /></div>
	{:else if !rows.rows.length}
		<p class="px-5 pt-5 text-[13px] text-muted-foreground">
			{rows.total ? 'No rows on this page.' : 'This version has no rows.'}
		</p>
	{:else}
		<div class={['overflow-x-auto', loading && 'opacity-60']} bind:clientWidth={width}>
			<!-- Short columns are pinned to their content (w-px), so the width left over goes to
			     the columns that wrap, or to a filler when none do. -->
			<table class="w-full border-collapse text-[13px]" bind:this={table}>
				<thead>
					<tr class="h-9 border-b border-divider text-left text-xs text-muted-foreground">
						<th class="w-px py-2 pr-2 pl-5 font-medium">#</th>
						{#each rows.columns as column, j (column.name)}
							{@const cap = capOf(j)}
							<!-- Headers stay on one line: one that wrapped would make the whole row taller.
							     A capped column cuts its header short instead. -->
							<th
								class={[
									'px-2 py-2 font-medium whitespace-nowrap last:pr-5',
									(cap !== null || !wraps[j]) && 'w-px'
								]}
								style:min-width={cap === null ? undefined : `${cap + PAD}px`}
								style:max-width={cap === null ? undefined : `${cap + PAD}px`}
							>
								<div class="truncate" {@attach hint(`${column.name} ${column.type}`)}>
									<span>{column.name}</span>
									<span
										class="ml-1 inline-block truncate align-bottom font-mono text-[11px] font-normal text-faint"
										style:max-width="{TYPE_MAX}px">{column.type}</span
									>
								</div>
							</th>
						{/each}
						{#if !filled || capped}<th></th>{/if}
					</tr>
				</thead>
				<tbody>
					{#each rows.rows as row, i (rows.offset + i)}
						{@const n = rows.offset + i}
						{@const open = opened.has(n)}
						<tr
							class={[
								'cursor-pointer border-b border-divider align-top',
								open ? 'bg-muted/60' : 'hover:bg-muted/60'
							]}
							onclick={(e) => onrowclick(e, n, row)}
						>
							<td class="py-1.5 pr-2 pl-5 text-muted-foreground tabular-nums">
								<button
									class="rounded-sm outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
									aria-expanded={open}
									aria-label="{open ? 'Close' : 'Open'} row {n}"
									onclick={() => toggle(n, row)}
								>
									{n.toLocaleString('en-US')}
								</button>
							</td>
							{#each row as cell, j (j)}
								{@const got = open ? full.get(`${n}:${rows.columns[j].name}`) : undefined}
								{@const cap = capOf(j)}
								<td
									class={[
										'px-2 py-1.5 last:pr-5',
										cap !== null
											? 'w-px wrap-anywhere'
											: wraps[j]
												? 'min-w-64'
												: 'w-px whitespace-nowrap'
									]}
									style:min-width={cap === null ? undefined : `${cap + PAD}px`}
									style:max-width={cap === null ? undefined : `${cap + PAD}px`}
								>
									<CellView
										cell={got && !(got instanceof Error) ? got : cell}
										full={open}
										wrap={wraps[j] || cap !== null}
										onopen={() => {
											if (cell.k === 'image')
												image.show({ cell, row: n, column: rows!.columns[j].name });
										}}
									/>
									{#if got instanceof Error}
										<p class="mt-1 text-[12px] text-destructive">{got.message}</p>
									{/if}
								</td>
							{/each}
							{#if !filled || capped}<td></td>{/if}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<div class="sticky bottom-3.5 z-5 w-fit pl-4">
			<PageNumbers
				{current}
				{pages}
				first={offset + 1}
				{last}
				{total}
				label="Row pages"
				ongo={(p) => go((p - 1) * size)}
				onstep={(step) => go(Math.max(0, offset + step * size))}
				{size}
				{sizes}
				onsize={(n) => {
					picked = n;
					// The page holding the first row shown.
					go(Math.floor(offset / n) * n);
				}}
			/>
		</div>
	{/if}
</div>

<ImageDialog
	bind:open={image.open}
	cell={image.value?.cell ?? null}
	column={image.value?.column ?? ''}
	row={image.value?.row ?? 0}
/>
