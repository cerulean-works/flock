<script lang="ts">
	import { cellCut, cellText } from '#lib/datasets.ts';
	import type { Cell } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';

	interface Props {
		cell: Cell;
		/** Open the whole cell in the side sheet (for cut text and JSON, and images). */
		onopen?: () => void;
	}

	let { cell, onopen }: Props = $props();

	const formatNumber = (v: number | string) =>
		typeof v === 'number' && Number.isInteger(v) ? v.toLocaleString('en-US') : String(v);
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
			class="h-12 max-w-32 rounded-sm object-contain"
		/>
	</button>
{:else if cell.k === 'bin'}
	<span class="text-muted-foreground tabular-nums">{bytes(cell.size)}</span>
{:else if cell.k === 'blob'}
	<span class="text-muted-foreground tabular-nums">
		{cell.size === null ? 'blob' : `blob, ${bytes(cell.size)}`}
	</span>
{:else if cell.k === 'vec'}
	<span class="font-mono text-[12px]">
		<span class="text-muted-foreground">{cell.dtype}[{cell.len}]</span>
		{cell.head.map((v) => (typeof v === 'number' ? Number(v.toPrecision(4)) : v)).join(', ')}{cell
			.head.length < cell.len
			? ', …'
			: ''}
	</span>
{:else}
	<!-- Up to four lines in the table: the rest is a click away, in the side sheet. -->
	<span class={['line-clamp-4 whitespace-pre-wrap', cell.k === 'json' && 'font-mono text-[12px]']}
		>{cell.v}{#if cellCut(cell)}…{/if}</span
	>
	{#if cellCut(cell)}
		<button
			class="text-[12px] whitespace-nowrap text-link underline-offset-3 hover:underline"
			onclick={onopen}
		>
			{(cell.len ?? 0).toLocaleString('en-US')} characters
		</button>
	{/if}
{/if}
