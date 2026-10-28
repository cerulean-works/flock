<script lang="ts">
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import type { Cell } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';

	interface Props {
		open: boolean;
		/** The image cell shown. */
		cell: Extract<Cell, { k: 'image' }> | null;
		column: string;
		row: number;
	}

	let { open = $bindable(), cell, column, row }: Props = $props();

	const src = $derived(cell ? `data:${cell.mime};base64,${cell.data}` : '');
	// Read off the image once it loads: the API sends only its bytes.
	let loaded = $state<{ src: string; w: number; h: number } | null>(null);
	const natural = $derived(loaded?.src === src ? loaded : null);
	// Icons and sprites scaled up stay crisp rather than turning to blur.
	const tiny = $derived(natural !== null && Math.max(natural.w, natural.h) <= 64);
</script>

<!-- One image of the row viewer, as large as the window allows, with what it's made of. -->
<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-[min(1100px,calc(100%-2rem))]">
		<Dialog.Header>
			<Dialog.Title class="font-mono font-normal">{column}</Dialog.Title>
			<Dialog.Description>Row {row.toLocaleString('en-US')}</Dialog.Description>
		</Dialog.Header>
		{#if cell}
			<div class="relative h-[min(70vh,800px)] rounded-lg bg-muted">
				<img
					{src}
					alt="{column}, row {row}"
					class={[
						'absolute inset-0 size-full object-contain p-2',
						tiny && '[image-rendering:pixelated]'
					]}
					onload={(e) => {
						const img = e.currentTarget as HTMLImageElement;
						loaded = { src, w: img.naturalWidth, h: img.naturalHeight };
					}}
				/>
			</div>
			<dl class="flex flex-wrap gap-x-6 gap-y-1 text-[13px]" aria-label="Image">
				<div class="flex gap-1.5">
					<dt class="text-muted-foreground">Dimensions</dt>
					<dd class="tabular-nums">
						{natural ? `${natural.w} × ${natural.h} pixels` : '…'}
					</dd>
				</div>
				<div class="flex gap-1.5">
					<dt class="text-muted-foreground">Size</dt>
					<dd class="tabular-nums">{bytes(cell.size)}</dd>
				</div>
				<div class="flex gap-1.5">
					<dt class="text-muted-foreground">Format</dt>
					<dd>{cell.mime.replace('image/', '').toUpperCase()}</dd>
				</div>
			</dl>
		{/if}
	</Dialog.Content>
</Dialog.Root>
