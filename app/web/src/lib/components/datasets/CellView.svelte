<script lang="ts">
	import { cellCut, cellText } from '#lib/datasets.ts';
	import { prettyJson } from '#lib/storage.ts';
	import type { Cell } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';

	interface Props {
		cell: Cell;
		/** In an opened row: all of it, unclamped, JSON laid out and vectors in full precision. */
		full?: boolean;
		/** Open an image in the image dialog. */
		onopen?: () => void;
	}

	let { cell, full = false, onopen }: Props = $props();

	const formatNumber = (v: number | string) =>
		typeof v === 'number' && Number.isInteger(v) ? v.toLocaleString('en-US') : String(v);
	const formatHead = (v: number | string | null) =>
		typeof v === 'number' && !full ? Number(v.toPrecision(4)) : v;
</script>

<!-- One cell of the row viewer, as the API rendered it (D90). -->
{#if cell.k === 'null'}
	<span class="text-faint">null</span>
{:else if cell.k === 'num'}
	<span class="tabular-nums">{formatNumber(cell.v)}</span>
	{#if cell.label !== undefined}<span class="text-muted-foreground">{cell.label}</span>{/if}
{:else if cell.k === 'bool'}
	<span>{cell.v}</span>
{:else if cell.k === 'time'}
	<span class="tabular-nums">{cell.v}</span>
{:else if cell.k === 'image'}
	<button
		class="block rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
		title={cellText(cell)}
		onclick={onopen}
	>
		<img
			src="data:{cell.mime};base64,{cell.data}"
			alt={cellText(cell)}
			class={['rounded-sm object-contain', full ? 'h-32 max-w-64' : 'h-12 max-w-32']}
		/>
	</button>
{:else if cell.k === 'bin'}
	<span class="text-muted-foreground tabular-nums">{bytes(cell.size)}</span>
{:else if cell.k === 'blob'}
	<span class="text-muted-foreground tabular-nums">
		{cell.size === null ? 'blob' : `blob, ${bytes(cell.size)}`}
	</span>
{:else if cell.k === 'vec'}
	<span class="font-mono text-[12px] break-words">
		<span class="text-muted-foreground">{cell.dtype}[{cell.len}]</span>
		{cell.head.map(formatHead).join(', ')}{cell.head.length < cell.len ? ', …' : ''}
	</span>
{:else if full}
	<!-- Long cells scroll in place, so a 1 MiB one doesn't stretch the table. -->
	<div class="max-h-[60vh] overflow-y-auto">
		<span class={['break-words whitespace-pre-wrap', cell.k === 'json' && 'font-mono text-[12px]']}
			>{cell.k === 'json' ? (prettyJson(cell.v) ?? cell.v) : cell.v}{#if cellCut(cell)}…{/if}</span
		>
	</div>
	{#if cellCut(cell)}
		<p class="mt-1 text-[12px] text-muted-foreground">
			{(cell.len ?? 0).toLocaleString('en-US')} characters
		</p>
	{/if}
{:else}
	<!-- Up to four lines in the table: the rest shows once the row is opened. -->
	<span class={['line-clamp-4 whitespace-pre-wrap', cell.k === 'json' && 'font-mono text-[12px]']}
		>{cell.v}{#if cellCut(cell)}…{/if}</span
	>
	{#if cellCut(cell)}
		<span class="text-[12px] whitespace-nowrap text-muted-foreground">
			{(cell.len ?? 0).toLocaleString('en-US')} characters
		</span>
	{/if}
{/if}
