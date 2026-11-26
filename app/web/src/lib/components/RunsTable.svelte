<script lang="ts" module>
	export const ROW_H = 40;
	export const HEAD_H = 40;
	export const BAND_H = 26;

	/** What the table shows: every column, the names beside the charts, or both side by side. */
	export type TableMode = 'table' | 'visualize' | 'charts';

	/** A suggested column's width: a little roomier than its content (see `primary`). */
	export const primaryWidth = (w: number) => Math.min(240, Math.round(w + 16));

	/** Kinds whose columns fit their content (up to their default width); others keep it. */
	const FITTED = new Set(['num', 'duration', 'str', 'bool', 'json']);
	const MIN_W = 64;
	/** A cell's padding (px-3 each side), and room for the header's sort arrow. */
	const PAD = 24;
	const ARROW = 14;
	/** Rows measured per page: enough to see its widest values, cheap on long pages. */
	const SAMPLE = 200;

	let measurer: CanvasRenderingContext2D | null | undefined;
	/** Rendered width of `text` in `font`; null where there's no canvas (the server). */
	export function textWidth(text: string, font: string): number | null {
		if (measurer === undefined)
			measurer =
				typeof document === 'undefined' ? null : document.createElement('canvas').getContext('2d');
		if (!measurer) return null;
		measurer.font = font;
		return measurer.measureText(text).width;
	}
</script>

<script lang="ts">
	import GripHorizontalIcon from '@lucide/svelte/icons/grip-horizontal';
	import { untrack } from 'svelte';
	import { hint } from '#lib/hint.svelte.ts';
	import { page as appPage } from '$app/state';
	import { prefetchRunPage } from '#lib/data/cache.ts';
	import { columnGroups } from '#lib/data/snapshot.ts';
	import type { ColumnDef, ColumnGroupSpan, RunQuery } from '#lib/types.ts';
	import {
		dateTime,
		duration,
		formatValue,
		numberFormat,
		range,
		relativeTime
	} from '#lib/utils.ts';
	import {
		LOCKED_COLUMNS,
		columnDrops,
		columnTree,
		listDrops,
		nearestGap
	} from '#lib/workspace/columns.ts';
	import { writeFilters } from '#lib/workspace/filters.ts';
	import { moveKey } from '#lib/workspace/layout.ts';
	import { applyRunQuery, runHref } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import Avatar from './Avatar.svelte';
	import ColumnResizer from './ColumnResizer.svelte';
	import RunEye from './RunEye.svelte';
	import RunMenu from './RunMenu.svelte';
	import StateBadge from './StateBadge.svelte';

	interface Props {
		mode: TableMode;
		/** The name column's width, which the viewer can drag. */
		nameW: number;
		/**
		 * Keys of the suggested columns, the ones that matter most here (a group's, 026): first,
		 * roomier, under a "Suggested" band of their own, ended by a bolder divider. The rest follow
		 * at the table's usual density. Keys the page has no column for are left out.
		 */
		primary?: readonly string[] | null;
		/**
		 * The viewer dragged the suggested columns into a new order (the keys shown, in it). The
		 * table shows that order until `primary` changes.
		 */
		onprimaryorder?: (keys: string[]) => void;
		/** In Visualize, above the charts rather than beside them. */
		stacked?: boolean;
	}

	let { mode, nameW, primary = null, onprimaryorder, stacked = false }: Props = $props();

	const ws = getWorkspace();
	const runs = $derived(ws.page.runs);
	/** Outside the org nothing can be changed: no selecting, no run menus (D116). */
	const readonly = $derived(appPage.data.shell?.access === 'public');
	const selectable = $derived(mode !== 'charts' && !readonly);
	/** The charts are beside the table: hovering a row focuses its run there. */
	const charting = $derived(mode !== 'table');
	/** Columns beside the names: every one, or none beside the charts alone. */
	const wide = $derived(mode !== 'charts');

	/** The suggested keys, in the order last dragged into here, until the page gives others. */
	let primaryKeys = $derived(primary);

	/** Indices into the project's columns of the suggested ones, in their order. */
	const primaryCols = $derived.by(() => {
		if (!primaryKeys?.length) return [];
		const out: number[] = [];
		for (const key of primaryKeys) {
			const i = ws.meta.columns.findIndex((c) => c.key === key);
			if (i !== -1 && !out.includes(i)) out.push(i);
		}
		return out;
	});
	const primaryCount = $derived(primaryCols.length);

	// The columns the viewer shows: `shown[c]` is shown column c's index into the project's
	// columns (and the page's cells); everything below indexes shown columns. Suggested columns
	// come first, whether or not the viewer hid them.
	const shown = $derived(
		primaryCount
			? [...primaryCols, ...ws.shownColumns.filter((i) => !primaryCols.includes(i))]
			: ws.shownColumns
	);
	const columns = $derived(shown.map((i) => ws.meta.columns[i]));
	/**
	 * Each column's header: its label, or, among the suggested columns (which have no group band),
	 * a metric's whole key when a setting there has the same label (`lr` and `summary.lr`).
	 */
	const labels = $derived(
		columns.map((col, c) =>
			c < primaryCount &&
			col.group === 'summary' &&
			columns.slice(0, primaryCount).some((o) => o !== col && o.label === col.label)
				? col.key
				: col.label
		)
	);
	const groups = $derived.by((): (Omit<ColumnGroupSpan, 'key'> & { key: string })[] => {
		if (!primaryCount) return columnGroups(columns);
		const rest = columnGroups(columns.slice(primaryCount)).map((g) => ({
			...g,
			start: g.start + primaryCount,
			end: g.end + primaryCount
		}));
		return [{ key: 'suggested', label: 'Suggested', start: 0, end: primaryCount }, ...rest];
	});

	// Widths are user-resizable. Overrides are kept by column key because the column list can
	// change from page to page as runs log new config or summary keys.
	let widthOverrides = $state.raw<Readonly<Record<string, number>>>({});
	/** The table's fonts, read once it's on screen; until then columns keep their defaults. */
	let fonts = $state<{ cell: string; head: string } | null>(null);
	/**
	 * Each column's width from its content: its header or its widest value on this page (a
	 * sample of rows), whichever is wider, within [MIN_W, its kind's default]. Measured per
	 * page, not per live update, so columns don't twitch as values tick.
	 */
	const fitted = $derived.by(() => {
		const f = fonts;
		const page = ws.page;
		return columns.map((col, c) => {
			if (!f || !FITTED.has(col.kind)) return col.width;
			let w = textWidth(labels[c], f.head) ?? col.width;
			w += ARROW;
			const rows = Math.min(page.runs.length, SAMPLE);
			for (let r = 0; r < rows; r++) {
				const text = untrack(() => cellText(c, r));
				if (text.length * 9 < w) continue; // can't be wider than what we have
				// Tabular digits run a little wider than the canvas measures them.
				w = Math.max(w, (textWidth(text, f.cell) ?? 0) * 1.06);
			}
			return Math.max(MIN_W, Math.min(col.width, Math.ceil(w + PAD)));
		});
	});
	const widths = $derived(
		columns.map(
			(c, i) => widthOverrides[c.key] ?? (i < primaryCount ? primaryWidth(fitted[i]) : fitted[i])
		)
	);
	const offsets = $derived.by(() => {
		const out = new Float64Array(widths.length + 1);
		for (let i = 0; i < widths.length; i++) out[i + 1] = out[i] + widths[i];
		return out;
	});
	const totalW = $derived(offsets[offsets.length - 1]);

	// Viewport; the SSR default assumes a laptop-sized window, corrected on mount.
	let scroller = $state<HTMLDivElement>();
	let viewW = $state(1440);
	let viewH = $state(900);
	let scrollX = $state(0);
	let scrollY = $state(0);

	const OVERSCAN_COLS = 3;
	const OVERSCAN_ROWS = 8;

	/** First column whose right edge is past `x`. */
	function colAt(x: number) {
		let lo = 0;
		let hi = widths.length - 1;
		while (lo < hi) {
			const mid = (lo + hi) >> 1;
			if (offsets[mid + 1] <= x) lo = mid + 1;
			else hi = mid;
		}
		return lo;
	}

	const colStart = $derived(!wide ? 0 : Math.max(0, colAt(scrollX) - OVERSCAN_COLS));
	const colEnd = $derived(
		!wide
			? 0
			: Math.min(widths.length, colAt(scrollX + Math.max(0, viewW - nameW)) + 1 + OVERSCAN_COLS)
	);
	// The sticky header covers the top of the viewport, so body rows are visible over
	// [scrollY, scrollY + viewH - header height] in body coordinates.
	const rowStart = $derived(Math.max(0, Math.floor(scrollY / ROW_H) - OVERSCAN_ROWS));
	const rowEnd = $derived(
		Math.min(runs.length, Math.ceil((scrollY + viewH - HEAD_H - BAND_H) / ROW_H) + OVERSCAN_ROWS)
	);

	const visibleCols = $derived(range(colStart, colEnd));
	const visibleRows = $derived(range(rowStart, rowEnd));

	function onscroll() {
		if (!scroller) return;
		scrollX = scroller.scrollLeft;
		scrollY = scroller.scrollTop;
	}

	function measure(el: HTMLDivElement) {
		const style = getComputedStyle(el);
		fonts = {
			cell: `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`,
			head: `500 12px ${style.fontFamily}`
		};
		const ro = new ResizeObserver(() => {
			viewW = el.clientWidth;
			viewH = el.clientHeight;
		});
		ro.observe(el);
		return () => ro.disconnect();
	}

	// A new page of runs (or other runs: a search, a filter) starts at the top; horizontal position is kept so sorting by a
	// column leaves that column in view.
	let lastQuery: RunQuery | null = null;
	$effect.pre(() => {
		const q = ws.page.query;
		const moved =
			lastQuery &&
			(q.page !== lastQuery.page ||
				q.sort !== lastQuery.sort ||
				q.search !== lastQuery.search ||
				writeFilters(q.filters) !== writeFilters(lastQuery.filters));
		if (moved && scroller) {
			scroller.scrollTop = 0;
			scrollY = 0;
		}
		lastQuery = q;
	});

	// ------------------------------------------------------------------------------------------
	// Cells

	/**
	 * Each numeric column's formatter, so a column writes its numbers one way (`numberFormat`);
	 * made when first needed, again for each page and table version.
	 */
	const formats = $derived.by(() => {
		void ws.tableVersion;
		void ws.page;
		return new Map<number, (v: number) => string>();
	});
	function formatIn(i: number): (v: number) => string {
		let f = formats.get(i);
		if (!f) formats.set(i, (f = numberFormat(ws.page.num[i] ?? [])));
		return f;
	}

	/** Cell text; reads the table version so live summary updates re-render visible cells. */
	function cellText(c: number, r: number): string {
		void ws.tableVersion;
		const col = columns[c];
		const i = shown[c];
		const nums = ws.page.num[i];
		const v = nums ? nums[r] : ws.page.text[i]?.[r];
		if (typeof v === 'number') {
			if (col.kind === 'time')
				return col.key === 'created_at' ? relativeTime(v, ws.meta.now) : dateTime(v);
			if (col.kind === 'duration') return duration(v);
			return formatIn(i)(v);
		}
		return formatValue(v);
	}

	const isNumeric = (col: ColumnDef) => col.kind === 'num' || col.kind === 'duration';

	/** The user id in a `user` cell (a run's owner), or null for none. */
	function userAt(c: number, r: number): number | null {
		const v = ws.page.num[shown[c]]?.[r];
		return v == null || Number.isNaN(v) ? null : v;
	}

	// ------------------------------------------------------------------------------------------
	// Sorting: server-side, one request per (sort, order); prefetched on hover intent.

	const query = $derived(ws.page.query);

	function sortQuery(key: string): RunQuery {
		const same = query.sort === key;
		return {
			...query,
			page: 1,
			sort: key,
			order: same && query.order === 'desc' ? 'asc' : 'desc'
		};
	}

	function sortHref(key: string) {
		const params = new URLSearchParams(appPage.url.search);
		applyRunQuery(params, sortQuery(key));
		return ws.href(params);
	}

	let intent: ReturnType<typeof setTimeout> | undefined;
	function hoverSort(key: string) {
		clearTimeout(intent);
		intent = setTimeout(
			() => prefetchRunPage(ws.meta.entity, ws.meta.project, sortQuery(key), ws.group),
			120
		);
	}
	const leaveSort = () => clearTimeout(intent);

	// ------------------------------------------------------------------------------------------
	// Moving columns: a header dragged sideways lands in the gap nearest the pointer. Suggested
	// columns move among themselves; the rest within their folder, as in the Columns menu (and
	// saved the same way). The header follows the pointer, a line marks where it would land, and
	// nothing moves until it's dropped; Escape cancels. A press that doesn't move still sorts.

	/** The header being dragged (shown column `c`), how far it has gone, and where it would land. */
	let drag = $state<{ c: number; dx: number; gap: number | null; moves: boolean } | null>(null);

	const DRAG_SLOP = 4; // px the pointer moves before a press becomes a drag
	const DRAG_EDGE = 48; // px from the table's sides where dragging scrolls it

	/** Whether shown column `c` can be dragged: a suggested one, or any but the locked ones. */
	const movable = (c: number) => c < primaryCount || !LOCKED_COLUMNS.has(columns[c].key);

	/** Where shown column `c` can be dropped, and what dropping it in a gap does. */
	function dropsFor(c: number) {
		if (c < primaryCount) {
			const keys = columns.slice(0, primaryCount).map((col) => col.key);
			return {
				from: c,
				gaps: listDrops(primaryCount, c),
				commit: (to: number) => {
					const next = moveKey(keys, c, to);
					primaryKeys = next;
					onprimaryorder?.(next);
				}
			};
		}
		const tree = columnTree(ws.arrangedColumns.map((i) => ws.meta.columns[i]));
		const rest = columns.slice(primaryCount).map((col) => col.key);
		const drops = columnDrops(tree, rest, columns[c].key);
		if (!drops) return null;
		return {
			from: drops.from,
			gaps: new Map([...drops.gaps].map(([g, to]) => [g + primaryCount, to])),
			commit: (to: number) => ws.moveColumn(drops.parent, drops.from, to)
		};
	}

	function grab(e: PointerEvent, c: number) {
		if (e.button !== 0 || !scroller || !movable(c)) return;
		const el = scroller;
		const drops = dropsFor(c);
		if (!drops) return;
		const x0 = e.clientX;
		const y0 = e.clientY;
		const sx0 = el.scrollLeft;
		let pointerX = x0;
		let started = false;
		let frame = 0;

		function follow() {
			if (!drag) return;
			drag.dx = pointerX - x0 + el.scrollLeft - sx0;
			const x = pointerX - el.getBoundingClientRect().left + el.scrollLeft;
			const gap = nearestGap(drops!.gaps, (g) => nameW + offsets[g], x);
			drag.gap = gap;
			drag.moves = gap !== null && drops!.gaps.get(gap) !== drops!.from;
		}

		// Held near a side, the table scrolls on under the pointer.
		function scroll() {
			frame = 0;
			if (!drag) return;
			const r = el.getBoundingClientRect();
			const left = r.left + nameW;
			const by =
				pointerX < left + DRAG_EDGE
					? -(left + DRAG_EDGE - pointerX) / 4
					: pointerX > r.right - DRAG_EDGE
						? (pointerX - (r.right - DRAG_EDGE)) / 4
						: 0;
			if (!by) return;
			el.scrollLeft += by;
			follow();
			frame = requestAnimationFrame(scroll);
		}

		// The release ends a drag with a click on the header's sort link: not a sort.
		const swallow = (ev: MouseEvent) => {
			ev.preventDefault();
			ev.stopPropagation();
			window.removeEventListener('click', swallow, true);
		};

		const move = (ev: PointerEvent) => {
			pointerX = ev.clientX;
			if (!started) {
				if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < DRAG_SLOP) return;
				started = true;
				leaveSort();
				window.getSelection()?.removeAllRanges();
				window.addEventListener('click', swallow, true);
				drag = { c, dx: 0, gap: null, moves: false };
			}
			follow();
			if (!frame) frame = requestAnimationFrame(scroll);
		};
		const cancel = (ev: KeyboardEvent) => {
			if (ev.key !== 'Escape' || !drag) return;
			ev.preventDefault();
			ev.stopPropagation();
			stop();
		};
		function stop() {
			cancelAnimationFrame(frame);
			frame = 0;
			drag = null;
			window.removeEventListener('pointermove', move);
			window.removeEventListener('keydown', cancel, true);
		}
		const up = (ev: PointerEvent) => {
			const done = drag;
			stop();
			window.removeEventListener('pointerup', up);
			window.removeEventListener('pointercancel', up);
			if (started) setTimeout(() => window.removeEventListener('click', swallow, true));
			if (ev.type === 'pointerup' && done?.moves && done.gap !== null) {
				drops.commit(drops.gaps.get(done.gap)!);
			}
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
		window.addEventListener('pointercancel', up);
		window.addEventListener('keydown', cancel, true);
	}

	// ------------------------------------------------------------------------------------------
	// Row hover feeds chart focus only when charts are showing.

	function rowFromEvent(e: Event) {
		const el = (e.target as HTMLElement).closest<HTMLElement>('[data-row]');
		return el ? Number(el.dataset.row) : null;
	}

	function onpointerover(e: PointerEvent) {
		if (!charting) return;
		const r = rowFromEvent(e);
		if (r != null && ws.hover !== r) ws.hover = r;
	}

	function onpointerleave() {
		if (charting) ws.hover = null;
	}

	/** The column last jumped to, marked for a moment so the eye finds it. */
	let jumped = $state<string | null>(null);
	let unmark: ReturnType<typeof setTimeout> | undefined;

	/** Scroll the table so a (shown) column starts at the left edge, just past the names. */
	export function scrollToColumn(key: string) {
		const c = columns.findIndex((col) => col.key === key);
		if (c === -1 || !scroller) return;
		scroller.scrollTo({ left: offsets[c] });
		jumped = key;
		clearTimeout(unmark);
		unmark = setTimeout(() => (jumped = null), 1200);
	}

	// Static class strings, shared by every row (hundreds of cells re-render while scrolling).
	const NAME_CELL =
		'name-cell sticky left-0 z-2 flex w-(--name-w) flex-none items-center gap-2.5 pr-3 pl-4 shadow-[inset_-1px_0_0_var(--divider)]';
	const CELL = 'cell flex flex-none items-center overflow-hidden px-3 whitespace-nowrap';
	const PINNED_BG = 'bg-[color-mix(in_srgb,var(--selection)_55%,var(--background))]';

	const allSelected = $derived(runs.length > 0 && ws.selectedRows.length === runs.length);
	const allShown = $derived(ws.visible.length === runs.length);
	const someShown = $derived(ws.visible.length > 0);
	const first = $derived((query.page - 1) * ws.meta.pageSize);
</script>

<!-- Beside the charts (`visualize`), the table is as wide as `--table-w`, and scrolls across;
     above them (stacked), it spans the page and is as tall as `--table-h`. -->
<div
	class={[
		'scroller absolute top-0 left-0 overflow-y-auto overscroll-contain pb-18 [contain:strict]',
		mode !== 'visualize'
			? 'right-0 bottom-0'
			: stacked
				? 'right-0 h-(--table-h)'
				: 'bottom-0 w-(--table-w)',
		wide ? 'overflow-x-auto' : 'overflow-x-hidden',
		drag && 'cursor-grabbing select-none'
	]}
	bind:this={scroller}
	{onscroll}
	{@attach measure}
	style:--name-w="{nameW}px"
	style:--row-h="{ROW_H}px"
>
	<div class="sticky top-0 z-3 bg-background" style:width="{nameW + totalW}px">
		<div class="relative flex" style:height="{BAND_H}px">
			<!-- The name column's divider runs up through the band, drawn the same way (its last
			     pixel); later groups' dividers sit on the same pixel as the header's. -->
			<div
				class="sticky left-0 z-2 w-(--name-w) flex-none bg-background shadow-[inset_-1px_0_0_var(--divider)]"
			></div>
			{#if wide}
				{#each groups as g (g.key)}
					<div
						class={[
							'absolute inset-y-0 text-xs text-muted-foreground',
							g.start > 0 && 'border-l border-divider'
						]}
						style:left="{nameW + offsets[g.start] - (g.start > 0 ? 1 : 0)}px"
						style:width="{offsets[g.end] - offsets[g.start]}px"
					>
						<span class="sticky left-[calc(var(--name-w)+12px)] inline-block px-3 pt-1.5">
							{g.label} <span class="ml-0.5 text-faint">{g.end - g.start}</span>
						</span>
					</div>
				{/each}
			{/if}
		</div>
		<div
			class="header flex items-stretch border-b text-xs font-medium text-muted-foreground"
			style:height="{HEAD_H}px"
		>
			<div class={[NAME_CELL, 'bg-background']}>
				{#if selectable}
					<Checkbox
						checked={allSelected}
						indeterminate={ws.selectedRows.length > 0 && !allSelected}
						onCheckedChange={() => ws.toggleSelectAll()}
						aria-label={allSelected ? 'Deselect all runs' : 'Select all runs on this page'}
					/>
				{:else}
					<!-- The checkbox's place stays, so names don't shift between the table and charts
					     (or between members and outsiders). -->
					<span class="size-4 flex-none"></span>
				{/if}
				<RunEye
					shown={allShown}
					partial={someShown && !allShown}
					label={allShown ? 'Hide all runs in charts' : 'Show all runs in charts'}
					onclick={() => ws.toggleAll()}
				/>
				<a
					class="flex h-full items-center gap-1 text-foreground"
					href={sortHref('name')}
					data-sveltekit-preload-data="tap"
					onpointerenter={() => hoverSort('name')}
					onpointerleave={leaveSort}
				>
					Name
					{#if query.sort === 'name'}<span class="arrow">{query.order === 'asc' ? '↑' : '↓'}</span
						>{/if}
				</a>
				<span class="ml-auto font-normal whitespace-nowrap text-faint">
					{#if !wide}
						{ws.visible.length} of {runs.length} plotted
					{:else}
						{first + 1}–{first + runs.length} of {ws.page.total}
					{/if}
				</span>
			</div>
			<div class="flex-none" style:width="{offsets[colStart]}px"></div>
			{#each visibleCols as c (c)}
				{@const col = columns[c]}
				{@const grabbable = wide && movable(c)}
				<!-- A press that moves drags the column; one that doesn't sorts by it. -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div
					class={[
						'hcell group/h relative flex flex-none items-center px-3 transition-colors duration-500 select-none',
						drag?.c === c ? 'opacity-40' : !drag && 'hover:bg-muted',
						jumped === col.key && 'bg-selection/60'
					]}
					style:width="{widths[c]}px"
					onpointerdown={grabbable ? (e) => grab(e, c) : undefined}
				>
					{#if grabbable && !drag}
						<span
							class="pointer-events-none absolute top-0 left-1/2 flex h-3 -translate-x-1/2 items-center text-faint opacity-0 group-hover/h:opacity-100"
							aria-hidden="true"
						>
							<GripHorizontalIcon class="size-3.5" />
						</span>
					{/if}
					<a
						class={['flex size-full min-w-0 items-center gap-1', isNumeric(col) && 'justify-end']}
						href={sortHref(col.key)}
						{@attach hint(grabbable ? `${col.key} (click to sort, drag to move)` : col.key)}
						draggable="false"
						data-sveltekit-preload-data="tap"
						onpointerenter={() => hoverSort(col.key)}
						onpointerleave={leaveSort}
					>
						<span class="label truncate">{labels[c]}</span>
						{#if query.sort === col.key}<span class="arrow text-foreground"
								>{query.order === 'asc' ? '↑' : '↓'}</span
							>{/if}
					</a>
					<ColumnResizer
						width={widths[c]}
						onresize={(w) => (widthOverrides = { ...widthOverrides, [col.key]: w })}
					/>
				</div>
			{/each}
		</div>
		{#if wide && primaryCount}
			<!-- Where the suggested columns end: a bolder divider, through the band and the rows. -->
			<div
				class="pointer-events-none absolute top-0 bottom-0 z-1 w-0.5 bg-border"
				style:left="{nameW + offsets[primaryCount] - 1}px"
			></div>
		{/if}
		{#if drag}
			{@const col = columns[drag.c]}
			{#if drag.moves && drag.gap !== null}
				<div
					class="pointer-events-none absolute top-0 bottom-0 z-1 w-0.5 bg-ring"
					style:left="{nameW + offsets[drag.gap] - 1}px"
				></div>
			{/if}
			<!-- The header, following the pointer. -->
			<div
				class={[
					'pointer-events-none absolute z-1 flex items-center gap-1 rounded-md bg-popover px-3 text-xs font-medium text-foreground shadow-lg ring-1 ring-foreground/15',
					isNumeric(col) && 'justify-end'
				]}
				style:top="{BAND_H + 4}px"
				style:height="{HEAD_H - 8}px"
				style:left="{nameW + offsets[drag.c] + drag.dx}px"
				style:width="{widths[drag.c]}px"
			>
				<span class="truncate">{col.label}</span>
			</div>
		{/if}
	</div>

	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="body relative"
		style:height="{runs.length * ROW_H}px"
		style:width="{nameW + totalW}px"
		{onpointerover}
		{onpointerleave}
	>
		{#if wide && primaryCount}
			<div
				class="pointer-events-none absolute inset-y-0 z-1 w-0.5 bg-border"
				style:left="{nameW + offsets[primaryCount] - 1}px"
			></div>
		{/if}
		{#if drag?.moves && drag.gap !== null}
			<div
				class="pointer-events-none absolute inset-y-0 z-1 w-0.5 bg-ring"
				style:left="{nameW + offsets[drag.gap] - 1}px"
			></div>
		{/if}
		{#each visibleRows as r (r)}
			{@const run = runs[r]}
			{@const shown = !ws.hidden.has(run.id)}
			{@const pinned = ws.pinned === r}
			{@const selected = selectable && ws.selected.has(run.id)}
			{@const focused = charting && ws.focus === r}
			<div
				class={[
					'row group/row absolute inset-x-0 flex h-(--row-h) items-stretch border-b border-divider tabular-nums',
					focused ? 'bg-muted' : selected ? 'bg-selection/45' : 'hover:bg-muted'
				]}
				data-row={r}
				style:top="{r * ROW_H}px"
			>
				<div
					class={[
						NAME_CELL,
						pinned || selected
							? PINNED_BG
							: focused
								? 'bg-muted'
								: 'bg-background group-hover/row:bg-muted'
					]}
				>
					{#if selectable}
						<Checkbox
							checked={selected}
							onCheckedChange={() => ws.toggleSelected(r)}
							aria-label="Select {ws.nameOf(r)}"
						/>
					{:else}
						<span class="size-4 flex-none"></span>
					{/if}
					<RunEye
						color={ws.colors[r]}
						{shown}
						label={shown ? `Hide ${ws.nameOf(r)} in charts` : `Show ${ws.nameOf(r)} in charts`}
						onclick={() => ws.toggleRun(r)}
					/>
					<!-- Loads on tap: hovering rows (charts follow them) shouldn't fetch run pages. -->
					<a
						class={[
							'run-name min-w-0 flex-1 truncate underline-offset-3 hover:text-link hover:underline',
							charting && !shown ? 'text-faint' : 'text-foreground'
						]}
						href={runHref(ws.meta.entity, ws.meta.project, run.id)}
						data-sveltekit-preload-data="tap"
						{@attach hint(ws.nameOf(r))}
					>
						{ws.nameOf(r)}
					</a>
					{#if pinned}<Badge variant="secondary" class="text-muted-foreground">Pinned</Badge>{/if}
					{#if ws.isLive(r)}
						<Badge
							variant="secondary"
							class="live bg-state-running/10 text-state-running"
							{@attach hint('Running')}
						>
							<span class="size-1.5 animate-live rounded-full bg-current motion-reduce:animate-none"
							></span>
							Live
						</Badge>
					{/if}
					{#if !readonly}<RunMenu row={r} />{/if}
				</div>
				<div class="flex-none" style:width="{offsets[colStart]}px"></div>
				{#each visibleCols as c (c)}
					{@const col = columns[c]}
					<div
						class={[
							CELL,
							isNumeric(col) && 'justify-end',
							(col.key === 'name' || col.key === 'commit') && 'font-mono text-[12.5px]'
						]}
						style:width="{widths[c]}px"
					>
						{#if col.kind === 'state'}
							<StateBadge state={ws.stateOf(r)} />
						{:else if col.kind === 'user'}
							{@const id = userAt(c, r)}
							{@const person = id == null ? undefined : ws.people.get(id)}
							{#if person}
								<Avatar name={person.name} src={person.avatar_url} class="mr-2 size-5 text-[9px]" />
								<span class="truncate" {@attach hint(person.email)}>{person.name}</span>
							{:else if id == null}
								<span class="text-faint">–</span>
							{:else}
								<!-- Someone who has left the organization since. -->
								<span class="truncate text-muted-foreground">user {id}</span>
							{/if}
						{:else}
							{@const text = cellText(c, r)}
							<span
								class={['truncate', text === '–' && 'text-faint']}
								{@attach hint(col.kind === 'str' ? text : undefined)}>{text}</span
							>
						{/if}
					</div>
				{/each}
			</div>
		{/each}
	</div>
</div>
