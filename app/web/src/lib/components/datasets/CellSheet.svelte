<script lang="ts">
	import * as Sheet from '#lib/components/ui/sheet/index.js';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import { prettyJson } from '#lib/storage.ts';
	import type { FullCell } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';
	import CellView from './CellView.svelte';

	interface Props {
		open: boolean;
		org: string;
		path: string;
		version: number;
		/** The cell to show, read when the sheet opens. */
		row: number;
		column: string;
	}

	let { open = $bindable(), org, path, version, row, column }: Props = $props();

	let full = $state<FullCell | null>(null);
	let failed = $state<string | null>(null);

	$effect(() => {
		if (!open) return;
		const controller = new AbortController();
		full = null;
		failed = null;
		datasetCall<FullCell>(datasetsApi(org, 'cell', { path, version, row, column }), {
			signal: controller.signal
		})
			.then((got) => (full = got))
			.catch((e: Error) => {
				if (!controller.signal.aborted) failed = e.message;
			});
		return () => controller.abort();
	});

	const text = $derived.by(() => {
		const cell = full?.cell;
		if (!cell) return null;
		if (cell.k === 'json') return prettyJson(cell.v) ?? cell.v;
		if (cell.k === 'str') return cell.v;
		return null;
	});
	// Longer than 1 MiB: the API sent the first 1 MiB, and the length.
	const cut = $derived(full?.cell.k === 'str' ? (full.cell.len ?? null) : null);
</script>

<!-- One cell in full, up to 1 MiB: long text, JSON and images. -->
<Sheet.Root bind:open>
	<Sheet.Content side="right" class="gap-0 p-0 sm:max-w-xl">
		<Sheet.Header class="border-b border-divider px-5 py-4">
			<Sheet.Title class="font-mono text-[15px] font-normal">{column}</Sheet.Title>
			<Sheet.Description>Row {row.toLocaleString('en-US')}</Sheet.Description>
		</Sheet.Header>
		<div class="min-h-0 overflow-y-auto px-5 py-4 text-[13px]">
			{#if failed}
				<p class="text-destructive" role="alert">{failed}</p>
			{:else if !full}
				<p class="text-muted-foreground">Loading…</p>
			{:else if full.cell.k === 'image'}
				<img
					src="data:{full.cell.mime};base64,{full.cell.data}"
					alt="{column}, row {row}"
					class="max-w-full"
				/>
				<p class="mt-2 text-muted-foreground">{bytes(full.cell.size)}</p>
			{:else if text !== null}
				<pre class="font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap">{text}</pre>
				{#if cut !== null}
					<p class="mt-2 text-muted-foreground">
						Showing the first {(1024 * 1024).toLocaleString('en-US')} of {cut.toLocaleString(
							'en-US'
						)} characters.
					</p>
				{/if}
			{:else}
				<CellView cell={full.cell} />
			{/if}
		</div>
	</Sheet.Content>
</Sheet.Root>
