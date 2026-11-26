<script lang="ts" module>
	/**
	 * Visualize's layout as the viewer last left it in this tab, shared across pages: the
	 * layout's data (from the cookies) is read once per visit, so a change made on one page would
	 * otherwise be undone on the next.
	 */
	const layout = $state<{ stacked: boolean | null; tableH: number | null }>({
		stacked: null,
		tableH: null
	});
</script>

<script lang="ts">
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import GitCompareArrowsIcon from '@lucide/svelte/icons/git-compare-arrows';
	import PinIcon from '@lucide/svelte/icons/pin';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import TagIcon from '@lucide/svelte/icons/tag';
	import ClipboardCheckIcon from '@lucide/svelte/icons/clipboard-check';
	import Columns2Icon from '@lucide/svelte/icons/columns-2';
	import Rows2Icon from '@lucide/svelte/icons/rows-2';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import XIcon from '@lucide/svelte/icons/x';
	import type { Snippet } from 'svelte';
	import { goto, refreshAll } from '$app/navigation';
	import { page } from '$app/state';
	import type { ResolvedPathname } from '$app/types';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Separator } from '#lib/components/ui/separator/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { clearRunPages, prefetchRunPage } from '#lib/data/cache.ts';
	import { LiveConnection, type ProjectEvent } from '#lib/data/live.ts';
	import { compareHref } from '#lib/evals.ts';
	import { EvalsView } from '#lib/evals-view.svelte.ts';
	import { hint } from '#lib/hint.svelte.ts';
	import { clampWidth, NAME_W, saveWidth, STACKED_COOKIE, TABLE_H, TABLE_W } from '#lib/widths.ts';
	import { describeFilters } from '#lib/workspace/filters.ts';
	import {
		isProjectView,
		rememberView,
		type ProjectView
	} from '#lib/workspace/project-view.svelte.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import ChartsPane from './ChartsPane.svelte';
	import ColumnsMenu from './ColumnsMenu.svelte';
	import DeleteRunsDialog from './DeleteRunsDialog.svelte';
	import EditRunDialog from './EditRunDialog.svelte';
	import EmptyState from './EmptyState.svelte';
	import EvalsTable from './EvalsTable.svelte';
	import FullscreenPanel from './FullscreenPanel.svelte';
	import MoveRunsDialog from './MoveRunsDialog.svelte';
	import Page from './Page.svelte';
	import PageHeader from './PageHeader.svelte';
	import Pager from './Pager.svelte';
	import PanelEditor from './PanelEditor.svelte';
	import PlotSettings from './PlotSettings.svelte';
	import ResizeHandle from './ResizeHandle.svelte';
	import RunFilters from './RunFilters.svelte';
	import RunSearch from './RunSearch.svelte';
	import RunsTable from './RunsTable.svelte';
	import TagRunsDialog from './TagRunsDialog.svelte';

	/**
	 * A page of runs with the Runs page's tools: the table, the charts, or both side by side,
	 * with search, filters, columns, plot settings, full-screen panels and the run dialogs. The
	 * Runs page shows the project's runs in it, and a group's page its members (026): the page
	 * makes the workspace (`setWorkspace`) with where its runs come from, and gives the header.
	 */
	interface Props {
		title: string;
		subtitleTitle?: string;
		leading?: Snippet;
		badges?: Snippet;
		subtitle?: Snippet;
		/** At the right of the title row (the live status). */
		actions?: Snippet;
		/** The charts' own controls, at the toolbar's right end while they show (a group's Display). */
		chartTools?: Snippet;
		/** At the toolbar's right end, before the charts' own controls. */
		trailing?: Snippet;
		/** Filter and Columns as icon buttons, for a toolbar with more in it (a group's). */
		compactTools?: boolean;
		/** The view a URL that names none opens in. */
		initialView: ProjectView;
		/** Whether a view picked here is the one the next project opens in (the Runs page). */
		remember?: boolean;
		/** The table's suggested columns (see RunsTable). */
		primary?: readonly string[] | null;
		/** The suggested columns were dragged into a new order (see RunsTable). */
		onprimaryorder?: (keys: string[]) => void;
		/** Shown in place of the runs when there are none, unless a search or filter hides them. */
		empty: Snippet;
		/** The project was renamed, deleted or made private (see LiveConnection). */
		onproject: (e: ProjectEvent) => void;
		/** The width of the name column, and of the table beside the charts, as last dragged. */
		nameW: number;
		tableW: number;
		/** Visualize with the table above the charts, as the viewer last left it (a cookie). */
		stacked?: boolean;
		/** The table's height above the charts, stacked. */
		tableH?: number;
		/** Outside the org: read-only, with no run actions (D116). */
		readonly: boolean;
		/** Whether the filters offer "My runs" (not among a group's few runs). */
		mine?: boolean;
		/** Whether the table offers its Evals preset (the Runs page). */
		evalPreset?: boolean;
		/** Whether the preset is on until the viewer turns it off (a group of eval runs). */
		evalsFirst?: boolean;
	}

	let {
		title,
		subtitleTitle,
		leading,
		badges,
		subtitle,
		actions,
		chartTools,
		trailing,
		compactTools = false,
		initialView,
		remember = false,
		primary = null,
		onprimaryorder,
		empty,
		onproject,
		nameW: savedNameW,
		tableW: savedTableW,
		stacked: savedStacked = false,
		tableH: savedTableH = TABLE_H.initial,
		readonly,
		mine = true,
		evalPreset = false,
		evalsFirst = false
	}: Props = $props();

	const ws = getWorkspace();

	const live = new LiveConnection(ws, (e) => onproject(e));
	$effect(() => {
		live.connect(ws.meta, ws.page);
		return () => live.close();
	});

	// Runs created or removed elsewhere, and metrics first logged, shift the page: it is read
	// again by itself, at most once every `RELOAD_MS` however many arrive, and not while the
	// viewer is in the middle of something it would disturb (that waits until it ends).
	const RELOAD_MS = 2000;
	const stale = $derived(ws.newRuns > 0 || ws.removedRuns > 0 || ws.newMetrics > 0);

	const OPEN_LAYER =
		'[data-slot="popover-content"], [data-slot="dropdown-menu-content"], [data-slot="select-content"]';

	/** A drag, a selection in the table, or an open dialog or menu. */
	const busy = () =>
		ws.dragging ||
		ws.selected.size > 0 ||
		ws.dialog != null ||
		ws.editing != null ||
		ws.unpinAsk != null ||
		!!document.querySelector(OPEN_LAYER);

	$effect(() => {
		if (!stale) return;
		let timer = setTimeout(function reload() {
			if (busy()) {
				timer = setTimeout(reload, RELOAD_MS);
				return;
			}
			// Counted again from the page read now (see LiveConnection).
			ws.newRuns = ws.removedRuns = ws.newMetrics = 0;
			clearRunPages();
			void refreshAll();
		}, RELOAD_MS);
		return () => clearTimeout(timer);
	});

	/** The runs' name column, beside the table's columns or the charts; the viewer can drag it. */
	let nameW = $derived(savedNameW);
	/** The table beside the charts (Visualize): always wider than its names. */
	let tableW = $derived(savedTableW || TABLE_W.initial);
	const shownTableW = $derived(Math.max(tableW, nameW + 160));
	/** Visualize stacked: the table above the charts. A second click on Visualize flips it. */
	const stacked = $derived(layout.stacked ?? savedStacked);
	function flipStacked() {
		layout.stacked = !stacked;
		const age = layout.stacked ? 31536000 : 0;
		document.cookie = `${STACKED_COOKIE}=1; path=/; max-age=${age}; samesite=lax`;
	}
	/**
	 * Whether Visualize was already on when its tab was pressed, so a click flips only then; and
	 * whether a key flipped it, so the click a key may also send doesn't flip it back.
	 */
	let visualizeOn = false;
	let flippedByKey = false;
	function onvisualizeclick(e: MouseEvent) {
		// A click with no press before it (`detail` 0: a key or assistive technology).
		const on = e.detail === 0 ? !flippedByKey && view === 'visualize' : visualizeOn;
		if (on) flipStacked();
		visualizeOn = flippedByKey = false;
	}
	/** The workspace's height; stacked, the table leaves the charts at least `CHARTS_MIN`. */
	let mainH = $state(0);
	const CHARTS_MIN = 200;
	const tableHMax = $derived(
		Math.min(TABLE_H.max, Math.max(TABLE_H.min, mainH ? mainH - CHARTS_MIN : TABLE_H.max))
	);
	const tableH = $derived(layout.tableH ?? savedTableH);
	/** The same limit in CSS, so the first paint, before the height is known, keeps it too. */
	const tableHCss = $derived(`min(${tableH}px, max(${TABLE_H.min}px, 100% - ${CHARTS_MIN}px))`);

	// URL is the source of truth for view and full-screen panel, so both survive reloads and
	// are server-rendered. Neither is read by a load function, so changing them never fetches.
	// Optimistic overrides make the switch land on the same frame as the click.
	let viewOverride = $state<ProjectView | null>(null);
	let panelOverride = $state<number | null | undefined>(undefined);

	/** The view whose URL names none: the table on the Runs page, else the one it opens in. */
	const plainView = $derived<ProjectView>(remember ? 'table' : initialView);

	const urlView = $derived.by((): ProjectView => {
		const v = page.url.searchParams.get('view');
		if (isProjectView(v)) return v;
		// A link to the Evals preset opens on the table it is a preset of.
		return evalPreset && page.url.searchParams.get('preset') === 'evals' ? 'table' : initialView;
	});
	const urlPanel = $derived.by(() => {
		const id = page.url.searchParams.get('panel');
		if (!id) return null;
		const i = ws.findPanel(id);
		return i === -1 ? null : i;
	});
	const view = $derived(viewOverride ?? urlView);
	// The Evals preset of the table: its eval runs by suites, ranked. Offered once a run has a
	// suite score (`flock eval log` writes `suites/<suite>`).
	const hasEvals = $derived(
		evalPreset && ws.meta.columns.some((c) => c.key.startsWith('summary.suites/'))
	);
	const preset = $derived(page.url.searchParams.get('preset'));
	const evals = $derived(hasEvals && (preset === 'evals' || (evalsFirst && preset === null)));
	/** The Evals table shows in place of the runs table (the table view only). */
	const evalsShown = $derived(evals && view === 'table');
	const evalsView = new EvalsView(ws);
	let evalsTable = $state<EvalsTable>();
	const fullscreen = $derived(panelOverride !== undefined ? panelOverride : urlPanel);

	function hrefWith(changes: Record<string, string | null>): ResolvedPathname {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- built for a link, never rendered
		const params = new URLSearchParams(page.url.search);
		for (const [k, v] of Object.entries(changes)) {
			if (v == null) params.delete(k);
			else params.set(k, v);
		}
		return ws.href(params);
	}

	const viewHref = (v: ProjectView) => hrefWith({ view: v === plainView ? null : v });
	/** A panel full screen, over the charts: from the table, over the charts alone. */
	const panelHref = (p: number) => {
		const under = view === 'table' ? 'charts' : view;
		return hrefWith({ panel: ws.panelDefs[p].id, view: under === plainView ? null : under });
	};

	function setView(v: ProjectView, e?: MouseEvent) {
		e?.preventDefault();
		if (v === view) return;
		viewOverride = v;
		if (remember) rememberView(v);
		ws.cursor = null;
		void goto(viewHref(v), { replace: true, reset: false }).finally(() => (viewOverride = null));
	}

	/** Set when this tab opened the panel, so closing can pop history instead of pushing. */
	let openedHere = false;

	function openPanel(m: number, e?: MouseEvent) {
		if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0)) return;
		e?.preventDefault();
		panelOverride = m;
		ws.cursor = null;
		const replace = fullscreen != null;
		if (!replace) openedHere = true;
		void goto(panelHref(m), { replace, reset: false }).finally(() => (panelOverride = undefined));
	}

	function closePanel(e?: MouseEvent) {
		e?.preventDefault();
		panelOverride = null;
		ws.cursor = null;
		ws.hover = null;
		if (openedHere) {
			openedHere = false;
			history.back();
			// popstate updates page.url; drop the override once it has.
			setTimeout(() => (panelOverride = undefined), 0);
		} else {
			void goto(hrefWith({ panel: null }), { replace: true, reset: false }).finally(
				() => (panelOverride = undefined)
			);
		}
	}

	const neighbours = $derived.by(() => {
		if (fullscreen == null) return { prev: null, next: null };
		const flat = ws.sections.flatMap((s) => s.panels);
		const i = flat.indexOf(fullscreen);
		return { prev: i > 0 ? flat[i - 1] : null, next: i < flat.length - 1 ? flat[i + 1] : null };
	});

	const KEYS: Record<string, ProjectView> = { t: 'table', v: 'visualize', c: 'charts' };

	function onkeydown(e: KeyboardEvent) {
		const t = e.target as HTMLElement;
		if (
			t.closest('input, textarea, select, [contenteditable]') ||
			e.metaKey ||
			e.ctrlKey ||
			e.altKey
		)
			return;
		// Open menus, popovers and the full-screen dialog handle their own keys (Escape included).
		if (t.closest('[role="menu"], [role="listbox"], [role="slider"], [role="tablist"]')) return;
		if (document.querySelector(OPEN_LAYER) || ws.editing != null) return;
		if (e.key === 'Escape') {
			if (fullscreen == null && view !== 'charts' && ws.selected.size) ws.selected.clear();
			else if (fullscreen == null) ws.pinnedId = null;
		} else if (fullscreen != null && e.key === 'ArrowLeft' && neighbours.prev != null) {
			openPanel(neighbours.prev);
		} else if (fullscreen != null && e.key === 'ArrowRight' && neighbours.next != null) {
			openPanel(neighbours.next);
		} else if (fullscreen == null && KEYS[e.key]) {
			setView(KEYS[e.key]);
		}
	}

	// Keep the neighbouring pages warm so paging is instant. Runs in idle time after hydration
	// and after every page change; the cache dedupes and keeps a handful of pages.
	$effect(() => {
		const q = ws.page.query;
		const pages = Math.ceil(ws.page.total / ws.meta.pageSize);
		const { entity, project } = ws.meta;
		const group = ws.group;
		const ric = window.requestIdleCallback ?? ((cb: () => void) => setTimeout(cb, 200));
		const id = ric(() => {
			if (q.page < pages) prefetchRunPage(entity, project, { ...q, page: q.page + 1 }, group);
			if (q.page > 1) prefetchRunPage(entity, project, { ...q, page: q.page - 1 }, group);
		});
		return () => (window.cancelIdleCallback ?? clearTimeout)(id);
	});

	let table = $state<RunsTable>();

	const focusLabel = $derived(ws.focus == null ? '' : ws.page.runs[ws.focus].name);
	const focusPinned = $derived(ws.focus != null && ws.pinned === ws.focus && ws.hover == null);

	/** The runs shown are a search's or a filter's, not all of the page's. */
	const narrowed = $derived(!!ws.page.query.search || ws.page.query.filters.length > 0);

	const noMatch = $derived.by(() => {
		const { search, filters } = ws.page.query;
		if (!filters.length) return `No run’s name or id contains “${search}”.`;
		const kindOf = (key: string) => ws.meta.columns.find((c) => c.key === key)?.kind;
		const where = describeFilters(filters, kindOf, false, ws.people);
		if (!search) return `No run matches ${where}.`;
		return `No run matching “${search}” has ${where}.`;
	});

	const tableShown = $derived(view !== 'charts');
	const chartsShown = $derived(view !== 'table');
	/** Runs are picked in the table: the toolbar is their actions'. */
	const selecting = $derived(tableShown && ws.selectedRows.length > 0 && !readonly);
	/** Where the charts start: past the names, or past the table beside them. */
	const below = $derived(view === 'visualize' && stacked);
	const paneLeft = $derived(below ? 0 : view === 'visualize' ? shownTableW : nameW);
</script>

<svelte:window {onkeydown} />

<Page
	width="full"
	data-sveltekit-reset={false}
	style="--name-w: {nameW}px; --table-w: {shownTableW}px; --table-h: {tableHCss}; --pane-left: {paneLeft}px"
>
	<PageHeader {title} {subtitleTitle} {leading} {badges} {subtitle} {actions}>
		{#snippet toolbar()}
			<Tabs.Root value={view} onValueChange={(v) => setView(v as ProjectView)}>
				<!-- No counts, so the toggle keeps its width from page to page. -->
				<Tabs.List aria-label="View">
					<Tabs.Trigger value="table" {@attach hint('Table (T)')}>Table</Tabs.Trigger>
					<!-- Clicked again while on, it flips the table and charts between side by side and
					     stacked; its icon shows which. -->
					<Tabs.Trigger
						value="visualize"
						onpointerdown={() => (visualizeOn = view === 'visualize')}
						onclick={onvisualizeclick}
						onkeydown={(e) => {
							if ((e.key === 'Enter' || e.key === ' ') && view === 'visualize') {
								flipStacked();
								flippedByKey = true;
							}
						}}
						{@attach hint(
							stacked
								? 'Table above the charts (V). Click again for side by side'
								: 'Table and charts side by side (V). Click again to stack them'
						)}
					>
						{#if stacked}<Rows2Icon />{:else}<Columns2Icon />{/if}
						Visualize
					</Tabs.Trigger>
					<Tabs.Trigger value="charts" {@attach hint('Charts (C)')}>Charts</Tabs.Trigger>
				</Tabs.List>
			</Tabs.Root>
			<Separator orientation="vertical" class="mx-1.5 h-5! data-vertical:self-center" />

			{#if selecting}
				<span class="selection-count mr-1 text-[13px] font-medium tabular-nums" role="status">
					{ws.selectedRows.length} selected
				</span>
				<!-- Two eval runs, doc by doc (025 D185): not offered for a run known not to be one. A
				pair without results files reaches the page, which says why. -->
				{#if ws.selectedRows.length === 2 && ws.selectedRows.every( (r) => [undefined, 'eval'].includes(ws.cell(r, 'job_type') as string | undefined) )}
					{@const [a, b] = ws.selectedRows.map((r) => ws.page.runs[r].id)}
					<Button
						variant="outline"
						data-sveltekit-preload-data="tap"
						href={compareHref(
							ws.meta.entity,
							ws.meta.project,
							a,
							`${ws.meta.entity}/${ws.meta.project}/${b}`
						)}
					>
						<GitCompareArrowsIcon /> Compare items
					</Button>
				{/if}
				<Button variant="outline" onclick={() => ws.openDialog('tag')}><TagIcon /> Tags</Button>
				<Button variant="outline" onclick={() => ws.openDialog('move')}>
					<FolderInputIcon /> Move
				</Button>
				<Button variant="outline" class="text-destructive" onclick={() => ws.openDialog('delete')}>
					<Trash2Icon /> Delete
				</Button>
				<Button
					variant="ghost"
					size="icon"
					{@attach hint('Clear selection (Esc)')}
					aria-label="Clear selection"
					onclick={() => ws.selected.clear()}
				>
					<XIcon />
				</Button>
			{:else}
				<RunSearch />
				<RunFilters noMine={!mine} compact={compactTools} />

				{#if tableShown}
					{#if hasEvals && view === 'table'}
						<Button
							variant={evals ? 'secondary' : 'outline'}
							aria-pressed={evals}
							href={hrefWith(
								evals
									? { preset: evalsFirst ? 'off' : null, view: 'table' }
									: { preset: evalsFirst ? null : 'evals' }
							)}
							data-sveltekit-replacestate
							{@attach hint('Eval runs by suites, ranked')}
						>
							<ClipboardCheckIcon /> Evals
						</Button>
					{/if}
					{#if evalsShown}
						<ColumnsMenu keys={evalsView.keys} onjump={(key) => evalsTable?.scrollToColumn(key)} />
					{:else}
						<ColumnsMenu onjump={(key) => table?.scrollToColumn(key)} compact={compactTools} />
					{/if}
				{/if}
				{#if chartsShown}
					<PlotSettings compact={compactTools} />
					{#if view === 'charts'}
						<Button variant="outline" onclick={() => ws.newPanel()}><PlusIcon /> Add panel</Button>
					{:else}
						<Button
							variant="outline"
							size="icon"
							{@attach hint('Add panel')}
							aria-label="Add panel"
							onclick={() => ws.newPanel()}
						>
							<PlusIcon />
						</Button>
					{/if}
				{/if}
			{/if}
			<!-- Outside the selection's actions too: the second run to compare may be one of these. -->
			{#if evalsShown && evalsView.others}
				<Button variant="outline" onclick={() => (evalsView.showOther = !evalsView.showOther)}>
					{evalsView.showOther ? 'Hide' : 'Show'}
					{evalsView.others} on other settings
				</Button>
			{/if}

			<span class="flex-1"></span>
			{#if view === 'charts' && focusLabel}
				<Badge
					variant="secondary"
					class="focus-label max-w-[46%] text-muted-foreground"
					{@attach hint(focusPinned ? `${focusLabel} (pinned)` : focusLabel)}
				>
					{#if focusPinned}<PinIcon aria-label="Pinned" />{/if}
					<span class="truncate">{focusLabel}</span>
				</Badge>
			{/if}
			{@render trailing?.()}
			{#if chartsShown && chartTools && !selecting}
				{@render chartTools()}
			{/if}
		{/snippet}
	</PageHeader>

	<main
		class="relative min-h-0 flex-1"
		bind:clientHeight={mainH}
		style:--pager-bottom={below ? 'calc(100% - var(--table-h) + 0.875rem)' : null}
	>
		{#if evalsShown}
			<EvalsTable bind:this={evalsTable} view={evalsView} />
		{:else}
			<RunsTable bind:this={table} mode={view} {nameW} {primary} {onprimaryorder} stacked={below} />
		{/if}
		<ChartsPane
			shown={chartsShown}
			active={chartsShown && fullscreen == null}
			{panelHref}
			onfullscreen={openPanel}
			{below}
		/>
		{#if ws.page.total === 0 && narrowed}
			<EmptyState
				variant="onboarding"
				class="empty absolute inset-0 z-4 overflow-y-auto bg-background"
				title="No matching runs"
			>
				{#snippet lead()}{noMatch}{/snippet}
				{#snippet actions()}
					<Button variant="outline" href={hrefWith({ q: null, filters: null, page: null })}>
						Show every run
					</Button>
				{/snippet}
			</EmptyState>
		{:else if ws.page.total === 0}
			{@render empty()}
		{:else}
			<Pager />
			<!-- On the name column's edge, above the table and the charts beside it. The Evals table
			     sizes its own run column. -->
			{#if !evalsShown}
				<div
					class={['pointer-events-none absolute top-0 left-0 z-5', !below && 'bottom-0']}
					style:width="{nameW}px"
					style:height={below ? 'var(--table-h)' : null}
				>
					<ResizeHandle
						class="pointer-events-auto"
						value={nameW}
						bounds={NAME_W}
						label="Resize the runs column"
						onresize={(px) => (nameW = px)}
						oncommit={(px) => saveWidth(NAME_W, px)}
					/>
				</div>
			{/if}
			{#if below}
				<!-- Between the table and the charts below it. -->
				<div class="pointer-events-none absolute inset-x-0 top-0 z-5 h-(--table-h)">
					<ResizeHandle
						class="pointer-events-auto"
						axis="y"
						value={Math.min(tableH, tableHMax)}
						bounds={{ ...TABLE_H, max: tableHMax }}
						label="Resize the table above the charts"
						onresize={(px) => (layout.tableH = px)}
						oncommit={(px) => saveWidth(TABLE_H, px)}
					/>
				</div>
			{:else if view === 'visualize'}
				<!-- Between the table and the charts. -->
				<div
					class="pointer-events-none absolute inset-y-0 left-0 z-5"
					style:width="{shownTableW}px"
				>
					<ResizeHandle
						class="pointer-events-auto"
						value={shownTableW}
						bounds={{ ...TABLE_W, min: Math.max(TABLE_W.min, nameW + 160) }}
						label="Resize the table beside the charts"
						onresize={(px) => (tableW = px)}
						oncommit={(px) => saveWidth(TABLE_W, clampWidth(TABLE_W, px))}
					/>
				</div>
			{/if}
		{/if}
	</main>
</Page>

{#if fullscreen != null}
	<FullscreenPanel
		panel={fullscreen}
		closeHref={hrefWith({ panel: null })}
		prevHref={neighbours.prev != null ? panelHref(neighbours.prev) : null}
		nextHref={neighbours.next != null ? panelHref(neighbours.next) : null}
		onclose={closePanel}
	/>
{/if}

<PanelEditor />
{#if !readonly}
	<EditRunDialog />
	<TagRunsDialog />
	<MoveRunsDialog />
	<DeleteRunsDialog />
{/if}
