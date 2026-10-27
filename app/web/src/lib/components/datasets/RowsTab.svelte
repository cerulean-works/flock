<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import Columns3Icon from '@lucide/svelte/icons/columns-3';
	import { SvelteSet } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { Cell, DatasetInfo, RowsPage } from '#lib/types.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import CellSheet from './CellSheet.svelte';
	import CellView from './CellView.svelte';

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

	let sheet = $state<{ row: number; column: string } | null>(null);
	const total = $derived(rows?.total ?? info.rows);
	const last = $derived(Math.min(offset + (rows?.rows.length ?? 0), total));
</script>

<div class="grid gap-3">
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

		<nav class="ml-auto flex items-center gap-1 text-[13px] tabular-nums" aria-label="Row pages">
			{#if total}
				<span>{(offset + 1).toLocaleString('en-US')}–{last.toLocaleString('en-US')}</span>
				<span class="mr-1 text-muted-foreground">of {total.toLocaleString('en-US')}</span>
			{/if}
			<Button
				variant="ghost"
				size="icon-sm"
				aria-label="Previous page"
				disabled={offset <= 0}
				onclick={() => go(Math.max(0, offset - size))}
			>
				<ChevronLeftIcon />
			</Button>
			<Button
				variant="ghost"
				size="icon-sm"
				aria-label="Next page"
				disabled={offset + size >= total}
				onclick={() => go(offset + size)}
			>
				<ChevronRightIcon />
			</Button>
		</nav>
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
					{#each rows.rows as row, i (offset + i)}
						<tr class="border-b border-divider align-top">
							<td class="px-2 py-1.5 text-right text-muted-foreground tabular-nums">
								{(rows.offset + i).toLocaleString('en-US')}
							</td>
							{#each row as cell, j (j)}
								<td class={['max-w-96 px-2 py-1.5', right(cell) && 'text-right']}>
									<CellView
										{cell}
										onopen={() =>
											(sheet = { row: rows!.offset + i, column: rows!.columns[j].name })}
									/>
								</td>
							{/each}
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<CellSheet
	bind:open={() => sheet !== null, (o) => (sheet = o ? sheet : null)}
	{org}
	{path}
	version={info.version}
	row={sheet?.row ?? 0}
	column={sheet?.column ?? ''}
/>
