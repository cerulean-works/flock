<script lang="ts" module>
	export const ROW_H = 36;
	export const HEAD_H = 48;
	/** The row-number column. */
	export const INDEX_W = 72;

	/** A column's starting width, from its Arrow type. */
	export function columnWidth(type: string): number {
		if (/^(bool)/.test(type)) return 88;
		if (/^(u?int|float|double|halffloat|decimal)/.test(type)) return 120;
		if (/^(timestamp|date|time)/.test(type)) return 200;
		if (/^(binary|large_binary|fixed_size_binary)/.test(type)) return 170;
		return 280;
	}
</script>

<script lang="ts">
	import ScissorsIcon from '@lucide/svelte/icons/scissors';
	import XIcon from '@lucide/svelte/icons/x';
	import { Button } from '#lib/components/ui/button/index.js';
	import { formatCell, isNumericType, shortType, truncatedCells } from '#lib/datasets.ts';
	import type { DatasetRows } from '#lib/types.ts';
	import { formatNumber, range } from '#lib/utils.ts';

	interface Props {
		/** The page of rows shown. */
		page: DatasetRows;
	}

	let { page }: Props = $props();

	const columns = $derived(page.columns);
	const cut = $derived(truncatedCells(page.truncated));

	// Widths can be dragged; kept by column name, so they survive paging.
	let widthOverrides = $state.raw<Readonly<Record<string, number>>>({});
	const widths = $derived(columns.map((c) => widthOverrides[c.name] ?? columnWidth(c.type)));
	const offsets = $derived.by(() => {
		const out = new Float64Array(widths.length + 1);
		for (let i = 0; i < widths.length; i++) out[i + 1] = out[i] + widths[i];
		return out;
	});
	const totalW = $derived(INDEX_W + offsets[offsets.length - 1]);

	// Viewport; the SSR default assumes a laptop-sized window, corrected on mount.
	let scroller = $state<HTMLDivElement>();
	let viewW = $state(1440);
	let viewH = $state(900);
	let scrollX = $state(0);
	let scrollY = $state(0);

	const OVERSCAN_COLS = 2;
	const OVERSCAN_ROWS = 8;

	/** First column whose right edge is past `x`. */
	function colAt(x: number) {
		let lo = 0;
		let hi = Math.max(0, widths.length - 1);
		while (lo < hi) {
			const mid = (lo + hi) >> 1;
			if (offsets[mid + 1] <= x) lo = mid + 1;
			else hi = mid;
		}
		return lo;
	}

	const colStart = $derived(Math.max(0, colAt(scrollX) - OVERSCAN_COLS));
	const colEnd = $derived(
		Math.min(widths.length, colAt(scrollX + Math.max(0, viewW - INDEX_W)) + 1 + OVERSCAN_COLS)
	);
	const rowStart = $derived(Math.max(0, Math.floor(scrollY / ROW_H) - OVERSCAN_ROWS));
	const rowEnd = $derived(
		Math.min(page.rows.length, Math.ceil((scrollY + viewH - HEAD_H) / ROW_H) + OVERSCAN_ROWS)
	);
	const visibleCols = $derived(range(colStart, colEnd));
	const visibleRows = $derived(range(rowStart, rowEnd));

	function onscroll() {
		if (!scroller) return;
		scrollX = scroller.scrollLeft;
		scrollY = scroller.scrollTop;
	}

	function measure(el: HTMLDivElement) {
		const ro = new ResizeObserver(() => {
			viewW = el.clientWidth;
			viewH = el.clientHeight;
		});
		ro.observe(el);
		return () => ro.disconnect();
	}

	// A cell opened in full below the table: long text, nested values.
	let open = $state<{ r: number; c: number } | null>(null);

	// A new page starts at the top, with nothing open.
	let last: DatasetRows | null = null;
	$effect.pre(() => {
		if (last && page !== last) {
			open = null;
			if (scroller && page.offset !== last.offset) {
				scroller.scrollTop = 0;
				scrollY = 0;
			}
		}
		last = page;
	});

	function startResize(e: PointerEvent, c: number) {
		e.preventDefault();
		e.stopPropagation();
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		const x0 = e.clientX;
		const w0 = widths[c];
		const name = columns[c].name;
		const move = (ev: PointerEvent) => {
			widthOverrides = {
				...widthOverrides,
				[name]: Math.max(64, Math.round(w0 + ev.clientX - x0))
			};
		};
		const up = () => {
			el.removeEventListener('pointermove', move);
			el.removeEventListener('pointerup', up);
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
	}

	const opened = $derived.by(() => {
		if (!open || open.r >= page.rows.length || open.c >= columns.length) return null;
		const value = page.rows[open.r][open.c];
		const cell = formatCell(value);
		const text = cell.kind === 'json' ? JSON.stringify(value, null, 2) : cell.text;
		return { ...open, text, kind: cell.kind, cut: cut.has(`${open.r}:${open.c}`) };
	});

	const CELL =
		'cell flex size-full items-center gap-1.5 overflow-hidden px-3 text-left whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset';
	const INDEX_CELL =
		'sticky left-0 z-2 flex flex-none items-center justify-end pr-3 text-xs text-muted-foreground tabular-nums shadow-[inset_-1px_0_0_var(--divider)]';
</script>

<div class="flex h-full min-h-0 flex-col">
	<div
		class="relative min-h-0 flex-1 overflow-auto overscroll-contain [contain:strict]"
		bind:this={scroller}
		{onscroll}
		{@attach measure}
		role="table"
		aria-label="Rows {formatNumber(page.offset + 1)} to {formatNumber(
			page.offset + page.rows.length
		)} of {formatNumber(page.total)}"
		aria-rowcount={page.rows.length + 1}
		aria-colcount={columns.length + 1}
	>
		<div
			class="sticky top-0 z-3 flex border-b bg-background"
			style:width="{totalW}px"
			style:height="{HEAD_H}px"
			role="row"
			aria-rowindex={1}
		>
			<div
				class={[INDEX_CELL, 'bg-background']}
				style:width="{INDEX_W}px"
				role="columnheader"
				aria-colindex={1}
			>
				<span class="sr-only">Row</span>
			</div>
			<div class="flex-none" style:width="{offsets[colStart]}px"></div>
			{#each visibleCols as c (c)}
				{@const col = columns[c]}
				<div
					class={[
						'group/h relative flex min-w-0 flex-none flex-col justify-center px-3',
						isNumericType(col.type) && 'items-end'
					]}
					style:width="{widths[c]}px"
					role="columnheader"
					aria-colindex={c + 2}
				>
					<span class="max-w-full truncate text-xs font-medium" title={col.name}>{col.name}</span>
					<span
						class="max-w-full truncate font-mono text-[11px] text-muted-foreground"
						title={col.type}>{shortType(col.type)}</span
					>
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						class="absolute top-2 -right-[3px] bottom-2 z-1 w-[7px] cursor-col-resize after:absolute after:inset-y-0 after:left-[3px] after:w-px after:bg-divider group-hover/h:after:bg-border"
						onpointerdown={(e) => startResize(e, c)}
					></div>
				</div>
			{/each}
		</div>

		<div
			class="relative"
			style:height="{page.rows.length * ROW_H}px"
			style:width="{totalW}px"
			role="rowgroup"
		>
			{#each visibleRows as r (r)}
				<div
					class="group/row absolute inset-x-0 flex border-b border-divider text-[13px] tabular-nums hover:bg-muted"
					style:top="{r * ROW_H}px"
					style:height="{ROW_H}px"
					role="row"
					aria-rowindex={r + 2}
				>
					<div
						class={[INDEX_CELL, 'bg-background group-hover/row:bg-muted']}
						style:width="{INDEX_W}px"
						role="rowheader"
						aria-colindex={1}
					>
						{formatNumber(page.offset + r)}
					</div>
					<div class="flex-none" style:width="{offsets[colStart]}px"></div>
					{#each visibleCols as c (c)}
						{@const col = columns[c]}
						{@const cell = formatCell(page.rows[r][c])}
						{@const isCut = cut.has(`${r}:${c}`)}
						{@const current = open?.r === r && open?.c === c}
						<div class="flex-none" style:width="{widths[c]}px" role="cell" aria-colindex={c + 2}>
							<button
								type="button"
								class={[
									CELL,
									isNumericType(col.type) && 'justify-end',
									(cell.kind === 'json' || cell.kind === 'binary') && 'font-mono text-[12px]',
									(cell.kind === 'null' || cell.kind === 'binary') && 'text-muted-foreground',
									current && 'bg-selection/60'
								]}
								title={isCut ? 'Cut short: open to read what was sent' : undefined}
								aria-expanded={current}
								onclick={() => (open = current ? null : { r, c })}
							>
								<span class="truncate">{cell.text}</span>
								{#if isCut}
									<ScissorsIcon class="size-3 flex-none text-muted-foreground" aria-hidden="true" />
									<span class="sr-only">(cut short)</span>
								{/if}
							</button>
						</div>
					{/each}
				</div>
			{/each}
		</div>
	</div>

	{#if opened}
		<section
			class="flex max-h-[40%] min-h-24 flex-none flex-col border-t"
			aria-label="Cell {columns[opened.c].name}, row {formatNumber(page.offset + opened.r)}"
		>
			<div class="flex h-10 flex-none items-center gap-2 px-4 text-[13px]">
				<span class="font-medium">{columns[opened.c].name}</span>
				<span class="text-muted-foreground">row {formatNumber(page.offset + opened.r)}</span>
				{#if opened.cut}
					<span class="text-muted-foreground">
						· cut short by Flock at 2,000 characters; read the dataset for the rest
					</span>
				{/if}
				<Button
					variant="ghost"
					size="icon-sm"
					class="ml-auto text-muted-foreground"
					aria-label="Close the cell"
					onclick={() => (open = null)}
				>
					<XIcon />
				</Button>
			</div>
			<pre
				class={[
					'min-h-0 flex-1 overflow-auto px-4 pb-4 text-[13px] leading-relaxed whitespace-pre-wrap',
					opened.kind === 'string' ? 'font-sans' : 'font-mono'
				]}>{opened.text}{#if opened.cut}…{/if}</pre>
		</section>
	{/if}
</div>
