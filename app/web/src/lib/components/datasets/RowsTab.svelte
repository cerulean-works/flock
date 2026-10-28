<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import Columns3Icon from '@lucide/svelte/icons/columns-3';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import { cellCut, datasetCall, datasetsApi, pageNumbers } from '#lib/datasets.ts';
	import type { Cell, DatasetInfo, FullCell, RowsPage } from '#lib/types.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import CellView from './CellView.svelte';
	import ImageDialog from './ImageDialog.svelte';

	interface Props {
		org: string;
		path: string;
		/** The dataset at the version shown: every read asks for that version. */
		info: DatasetInfo;
	}

	let { org, path, info }: Props = $props();

	// Pages by offset, kept in the URL (D90): 50 rows, or 10 with binary columns.
	const size = $derived(info.binary ? 10 : 50);
	const offset = $derived(
		Math.max(0, Math.floor(Number(page.url.searchParams.get('offset')) || 0))
	);

	/** Columns the viewer hid; they aren't read. */
	const hidden = new SvelteSet<string>();
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
		datasetCall<RowsPage>(datasetsApi(org, 'rows', query), { signal: controller.signal })
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

	const RIGHT = new Set(['num', 'bool']);
	const right = (cell: Cell | undefined) => !!cell && RIGHT.has(cell.k);

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
				datasetsApi(org, 'cell', { path, version: info.version, row, column: column.name })
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

	let image = $state<{ cell: Extract<Cell, { k: 'image' }>; row: number; column: string } | null>(
		null
	);
</script>

<div class="bleed grid gap-3">
	<div class="flex flex-wrap items-center gap-2">
		<Popover.Root>
			<Popover.Trigger>
				{#snippet child({ props })}
					<Button {...props} variant="outline" size="sm">
						<Columns3Icon />
						Columns
						{#if hidden.size}<span class="text-muted-foreground"
								>{shown.length} of {names.length}</span
							>{/if}
					</Button>
				{/snippet}
			</Popover.Trigger>
			<Popover.Content align="start" class="max-h-96 w-64 gap-1 overflow-y-auto">
				<div class="flex items-center justify-between pb-1">
					<p class="text-xs text-muted-foreground">Columns shown</p>
					{#if hidden.size}
						<button class="text-xs text-link hover:underline" onclick={() => hidden.clear()}>
							Show all
						</button>
					{/if}
				</div>
				{#each names as name (name)}
					<label class="flex items-center gap-2 py-1 text-[13px]">
						<Checkbox
							checked={!hidden.has(name)}
							onCheckedChange={(on) => (on ? hidden.delete(name) : hidden.add(name))}
						/>
						<span class="truncate font-mono text-[12.5px]">{name}</span>
					</label>
				{/each}
			</Popover.Content>
		</Popover.Root>
	</div>

	{#if !shown.length}
		<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Pick a column to show.</p>
	{:else if failed}
		<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{failed}</p>
	{:else if !rows}
		<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
	{:else if !rows.rows.length}
		<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">
			{rows.total ? 'No rows on this page.' : 'This version has no rows.'}
		</p>
	{:else}
		<div class={['overflow-x-auto', loading && 'opacity-60']}>
			<table class="w-max min-w-full border-collapse text-[13px]">
				<thead>
					<tr class="h-9 border-b text-left text-xs text-muted-foreground">
						<th class="w-14 px-2 text-right font-medium">#</th>
						{#each rows.columns as column (column.name)}
							<th
								class={[
									'px-2 font-medium',
									right(rows.rows[0]?.[rows.columns.indexOf(column)]) && 'text-right'
								]}
							>
								<span class="text-foreground">{column.name}</span>
								<span class="ml-1 font-mono text-[11px] font-normal">{column.type}</span>
							</th>
						{/each}
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
							<td class="px-2 py-1.5 text-right text-muted-foreground tabular-nums">
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
								<td class={['max-w-96 px-2 py-1.5', right(cell) && 'text-right']}>
									<CellView
										cell={got && !(got instanceof Error) ? got : cell}
										full={open}
										onopen={() => {
											if (cell.k === 'image')
												image = { cell, row: n, column: rows!.columns[j].name };
										}}
									/>
									{#if got instanceof Error}
										<p class="mt-1 text-[12px] text-destructive">{got.message}</p>
									{/if}
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<nav
			class="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] tabular-nums"
			aria-label="Row pages"
		>
			<p>
				{(offset + 1).toLocaleString('en-US')}–{last.toLocaleString('en-US')}
				<span class="text-muted-foreground">of {total.toLocaleString('en-US')}</span>
			</p>
			<div class="ml-auto flex items-center gap-1">
				<Button
					variant="ghost"
					size="sm"
					aria-label="Previous page"
					disabled={current <= 1}
					onclick={() => go(Math.max(0, offset - size))}
				>
					<ChevronLeftIcon /> Previous
				</Button>
				{#each pageNumbers(current, pages) as p, i (p ?? `gap${i}`)}
					{#if p === null}
						<span class="w-7 text-center text-muted-foreground" aria-hidden="true">…</span>
					{:else}
						<Button
							variant={p === current ? 'secondary' : 'ghost'}
							size="sm"
							class="min-w-7 px-1.5"
							aria-label="Page {p}"
							aria-current={p === current ? 'page' : undefined}
							onclick={() => go((p - 1) * size)}
						>
							{p.toLocaleString('en-US')}
						</Button>
					{/if}
				{/each}
				<Button
					variant="ghost"
					size="sm"
					aria-label="Next page"
					disabled={current >= pages}
					onclick={() => go(offset + size)}
				>
					Next <ChevronRightIcon />
				</Button>
			</div>
		</nav>
	{/if}
</div>

<ImageDialog
	bind:open={() => image !== null, (o) => (image = o ? image : null)}
	cell={image?.cell ?? null}
	column={image?.column ?? ''}
	row={image?.row ?? 0}
/>
