<script lang="ts" module>
	export const ROW_H = 40;
	export const HEAD_H = 40;
	export const BAND_H = 26;
</script>

<script lang="ts">
	import { page as appPage } from '$app/state';
	import { prefetchRunPage } from '#lib/data/cache.ts';
	import { columnGroups } from '#lib/data/snapshot.ts';
	import type { ColumnDef, RunQuery } from '#lib/types.ts';
	import {
		dateTime,
		duration,
		formatNumber,
		formatValue,
		range,
		relativeTime
	} from '#lib/utils.ts';
	import { writeFilters } from '#lib/workspace/filters.ts';
	import { applyRunQuery, runHref, workspaceHref } from '#lib/workspace/url.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import RunEye from './RunEye.svelte';
	import RunMenu from './RunMenu.svelte';
	import StateBadge from './StateBadge.svelte';

	interface Props {
		mode: 'table' | 'charts';
		/** The name column's width, which the viewer can drag. */
		nameW: number;
	}

	let { mode, nameW }: Props = $props();

	const ws = getWorkspace();
	const runs = $derived(ws.page.runs);
	// The columns the viewer shows: `shown[c]` is shown column c's index into the project's
	// columns (and the page's cells); everything below indexes shown columns.
	const shown = $derived(ws.shownColumns);
	const columns = $derived(shown.map((i) => ws.meta.columns[i]));
	const groups = $derived(columnGroups(columns));

	// Widths are user-resizable. Overrides are kept by column key because the column list can
	// change from page to page as runs log new config or summary keys.
	let widthOverrides = $state.raw<Readonly<Record<string, number>>>({});
	const widths = $derived(columns.map((c) => widthOverrides[c.key] ?? c.width));
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

	const colStart = $derived(mode === 'charts' ? 0 : Math.max(0, colAt(scrollX) - OVERSCAN_COLS));
	const colEnd = $derived(
		mode === 'charts'
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
			return formatNumber(v);
		}
		return formatValue(v);
	}

	const isNumeric = (col: ColumnDef) => col.kind === 'num' || col.kind === 'duration';

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
		return workspaceHref(ws.meta.entity, ws.meta.project, params);
	}

	let intent: ReturnType<typeof setTimeout> | undefined;
	function hoverSort(key: string) {
		clearTimeout(intent);
		intent = setTimeout(
			() => prefetchRunPage(ws.meta.entity, ws.meta.project, sortQuery(key)),
			120
		);
	}
	const leaveSort = () => clearTimeout(intent);

	// ------------------------------------------------------------------------------------------
	// Column resize

	function startResize(e: PointerEvent, c: number) {
		e.preventDefault();
		e.stopPropagation();
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		const x0 = e.clientX;
		const w0 = widths[c];
		const key = columns[c].key;
		const move = (ev: PointerEvent) => {
			widthOverrides = { ...widthOverrides, [key]: Math.max(56, Math.round(w0 + ev.clientX - x0)) };
		};
		const up = () => {
			el.removeEventListener('pointermove', move);
			el.removeEventListener('pointerup', up);
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
	}

	// ------------------------------------------------------------------------------------------
	// Row hover feeds chart focus only when charts are showing.

	function rowFromEvent(e: Event) {
		const el = (e.target as HTMLElement).closest<HTMLElement>('[data-row]');
		return el ? Number(el.dataset.row) : null;
	}

	function onpointerover(e: PointerEvent) {
		if (mode !== 'charts') return;
		const r = rowFromEvent(e);
		if (r != null && ws.hover !== r) ws.hover = r;
	}

	function onpointerleave() {
		if (mode === 'charts') ws.hover = null;
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

<div
	class={[
		'scroller absolute inset-0 overflow-y-auto overscroll-contain pb-18 [contain:strict]',
		mode === 'charts' ? 'overflow-x-hidden' : 'overflow-x-auto'
	]}
	bind:this={scroller}
	{onscroll}
	{@attach measure}
	style:--name-w="{nameW}px"
	style:--row-h="{ROW_H}px"
>
	<div class="sticky top-0 z-3 bg-background" style:width="{nameW + totalW}px">
		<div class="relative flex" style:height="{BAND_H}px">
			<div class="sticky left-0 z-2 w-(--name-w) flex-none bg-background"></div>
			{#if mode === 'table'}
				{#each groups as g (g.key)}
					<div
						class="absolute inset-y-0 border-l border-divider text-xs text-muted-foreground"
						style:left="{nameW + offsets[g.start]}px"
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
				{#if mode === 'table'}
					<Checkbox
						checked={allSelected}
						indeterminate={ws.selectedRows.length > 0 && !allSelected}
						onCheckedChange={() => ws.toggleSelectAll()}
						aria-label={allSelected ? 'Deselect all runs' : 'Select all runs on this page'}
					/>
				{:else}
					<!-- The checkbox's place stays, so names don't shift between the table and charts. -->
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
					{#if mode === 'charts'}
						{ws.visible.length} of {runs.length} plotted
					{:else}
						{first + 1}–{first + runs.length} of {ws.page.total}
					{/if}
				</span>
			</div>
			<div class="flex-none" style:width="{offsets[colStart]}px"></div>
			{#each visibleCols as c (c)}
				{@const col = columns[c]}
				<div
					class={[
						'hcell group/h relative flex flex-none items-center px-3 transition-colors duration-500 hover:bg-muted',
						jumped === col.key && 'bg-selection/60'
					]}
					style:width="{widths[c]}px"
				>
					<a
						class={['flex size-full min-w-0 items-center gap-1', isNumeric(col) && 'justify-end']}
						href={sortHref(col.key)}
						title={col.key}
						data-sveltekit-preload-data="tap"
						onpointerenter={() => hoverSort(col.key)}
						onpointerleave={leaveSort}
					>
						<span class="label truncate">{col.label}</span>
						{#if query.sort === col.key}<span class="arrow text-foreground"
								>{query.order === 'asc' ? '↑' : '↓'}</span
							>{/if}
					</a>
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						class="absolute top-2 -right-[3px] bottom-2 z-1 w-[7px] cursor-col-resize after:absolute after:inset-y-0 after:left-[3px] after:w-px after:bg-divider group-hover/h:after:bg-border"
						onpointerdown={(e) => startResize(e, c)}
					></div>
				</div>
			{/each}
		</div>
	</div>

	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="body relative"
		style:height="{runs.length * ROW_H}px"
		style:width="{nameW + totalW}px"
		{onpointerover}
		{onpointerleave}
	>
		{#each visibleRows as r (r)}
			{@const run = runs[r]}
			{@const shown = !ws.hidden.has(run.id)}
			{@const pinned = ws.pinned === r}
			{@const selected = mode === 'table' && ws.selected.has(run.id)}
			{@const focused = mode === 'charts' && ws.focus === r}
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
					{#if mode === 'table'}
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
							mode === 'charts' && !shown ? 'text-faint' : 'text-foreground'
						]}
						href={runHref(ws.meta.entity, ws.meta.project, run.id)}
						data-sveltekit-preload-data="tap"
						title={ws.nameOf(r)}
					>
						{ws.nameOf(r)}
					</a>
					{#if pinned}<Badge variant="secondary" class="text-muted-foreground">Pinned</Badge>{/if}
					{#if ws.isLive(r)}
						<Badge
							variant="secondary"
							class="live bg-state-running/10 text-state-running"
							title="Running"
						>
							<span class="size-1.5 animate-live rounded-full bg-current motion-reduce:animate-none"
							></span>
							Live
						</Badge>
					{/if}
					<RunMenu row={r} />
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
						{:else}
							{@const text = cellText(c, r)}
							<span
								class={['truncate', text === '–' && 'text-faint']}
								title={col.kind === 'str' ? text : undefined}>{text}</span
							>
						{/if}
					</div>
				{/each}
			</div>
		{/each}
	</div>
</div>
