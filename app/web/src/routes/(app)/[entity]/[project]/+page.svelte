<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto, refreshAll } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import ChartsPane from '#lib/components/ChartsPane.svelte';
	import ColumnsMenu from '#lib/components/ColumnsMenu.svelte';
	import FullscreenPanel from '#lib/components/FullscreenPanel.svelte';
	import PanelEditor from '#lib/components/PanelEditor.svelte';
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import TagIcon from '@lucide/svelte/icons/tag';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import XIcon from '@lucide/svelte/icons/x';
	import { toast } from 'svelte-sonner';
	import DeleteRunsDialog from '#lib/components/DeleteRunsDialog.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import EditRunDialog from '#lib/components/EditRunDialog.svelte';
	import MoveRunsDialog from '#lib/components/MoveRunsDialog.svelte';
	import LiveStatus from '#lib/components/LiveStatus.svelte';
	import Pager from '#lib/components/Pager.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import PlotSettings from '#lib/components/PlotSettings.svelte';
	import RunFilters from '#lib/components/RunFilters.svelte';
	import { describeFilters } from '#lib/workspace/filters.ts';
	import RunSearch from '#lib/components/RunSearch.svelte';
	import TagRunsDialog from '#lib/components/TagRunsDialog.svelte';
	import ResizeHandle from '#lib/components/ResizeHandle.svelte';
	import RunsTable from '#lib/components/RunsTable.svelte';
	import { NAME_W, saveWidth } from '#lib/widths.ts';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Separator } from '#lib/components/ui/separator/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { clearRunPages, prefetchRunPage } from '#lib/data/cache.ts';
	import { LiveConnection, type ProjectEvent } from '#lib/data/live.ts';
	import { entityHref, workspaceHrefWith } from '#lib/workspace/url.ts';
	import { Workspace, setWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import { lastView, rememberView, type ProjectView } from '#lib/workspace/project-view.svelte.ts';
	import Page from '#lib/components/Page.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const ws = new Workspace(
		() => data.meta,
		() => data.runs
	);
	setWorkspace(ws);

	// Lets performance tooling measure navigation-start → interactive.
	onMount(() => performance.mark('workspace:hydrated'));

	// The viewer's panel arrangement for this project, once hydrated (the server can't know it).
	$effect(() => {
		void [ws.meta.entity, ws.meta.project];
		untrack(() => ws.restoreLayout());
	});

	// Live updates for the runs on the page being shown. A different page (paging, sorting, a
	// refresh) resubscribes with that page's cursors and replays whatever it missed.
	// Someone else renamed or deleted the project: follow it, or leave.
	// The same toast id as the dialogs' own, so a change made here isn't announced twice.
	function onproject(e: ProjectEvent) {
		const { entity, project } = ws.meta;
		if (e.deleted) {
			toast(`${project} was deleted`, { id: 'project' });
			void goto(entityHref(entity), { replaceState: true });
			return;
		}
		const to = e.renamed_to ?? project;
		toast(`Project renamed to ${to}`, { id: 'project' });
		clearRunPages();
		void goto(workspaceHrefWith(entity, to, page.url.search, {}), {
			replaceState: true
		});
	}

	const live = new LiveConnection(ws, onproject);
	$effect(() => {
		live.connect(ws.meta, ws.page);
		return () => live.close();
	});

	let refreshing = $state(false);
	async function showNewRuns() {
		refreshing = true;
		clearRunPages();
		try {
			await refreshAll();
		} finally {
			refreshing = false;
		}
	}

	/** The runs' name column, beside the table's columns or the charts; the viewer can drag it. */
	let nameW = $derived(data.shell.nameW);

	type View = ProjectView;

	// URL is the source of truth for view and full-screen panel, so both survive reloads and
	// are server-rendered. Neither is read by a load function, so changing them never fetches.
	// Optimistic overrides make the switch land on the same frame as the click.
	let viewOverride = $state<View | null>(null);
	let panelOverride = $state<number | null | undefined>(undefined);

	// A URL without a view (a project opened from the sidebar) shows the one picked last.
	const urlView = $derived.by((): View => {
		const v = page.url.searchParams.get('view');
		return v === 'charts' || v === 'table' ? v : lastView(data.shell.projectView);
	});
	const urlPanel = $derived.by(() => {
		const id = page.url.searchParams.get('panel');
		if (!id) return null;
		const i = ws.findPanel(id);
		return i === -1 ? null : i;
	});
	const view = $derived(viewOverride ?? urlView);
	const fullscreen = $derived(panelOverride !== undefined ? panelOverride : urlPanel);

	const hrefWith = (changes: Record<string, string | null>) =>
		workspaceHrefWith(ws.meta.entity, ws.meta.project, page.url.search, changes);

	const viewHref = (v: View) => hrefWith({ view: v === 'table' ? null : v });
	const panelHref = (p: number) => hrefWith({ panel: ws.meta.panels[p].id, view: 'charts' });

	function setView(v: View, e?: MouseEvent) {
		e?.preventDefault();
		if (v === view) return;
		viewOverride = v;
		rememberView(v);
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

	const OPEN_LAYER =
		'[data-slot="popover-content"], [data-slot="dropdown-menu-content"], [data-slot="select-content"]';

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
			if (fullscreen == null && view === 'table' && ws.selected.size) ws.selected.clear();
			else if (fullscreen == null) ws.pinnedId = null;
		} else if (fullscreen != null && e.key === 'ArrowLeft' && neighbours.prev != null) {
			openPanel(neighbours.prev);
		} else if (fullscreen != null && e.key === 'ArrowRight' && neighbours.next != null) {
			openPanel(neighbours.next);
		} else if (fullscreen == null && e.key === 't') {
			setView('table');
		} else if (fullscreen == null && e.key === 'c') {
			setView('charts');
		}
	}

	// Keep the neighbouring pages warm so paging is instant. Runs in idle time after hydration
	// and after every page change; the cache dedupes and keeps a handful of pages.
	$effect(() => {
		const q = ws.page.query;
		const pages = Math.ceil(ws.page.total / ws.meta.pageSize);
		const { entity, project } = ws.meta;
		const ric = window.requestIdleCallback ?? ((cb: () => void) => setTimeout(cb, 200));
		const id = ric(() => {
			if (q.page < pages) prefetchRunPage(entity, project, { ...q, page: q.page + 1 });
			if (q.page > 1) prefetchRunPage(entity, project, { ...q, page: q.page - 1 });
		});
		return () => (window.cancelIdleCallback ?? clearTimeout)(id);
	});

	let table = $state<RunsTable>();

	const focusLabel = $derived.by(() => {
		const f = ws.focus;
		if (f == null) return '';
		return `${ws.pinned === f && ws.hover == null ? 'Pinned · ' : ''}${ws.page.runs[f].name}`;
	});

	const updatesLabel = $derived(
		[
			ws.newRuns && `${ws.newRuns} new ${ws.newRuns === 1 ? 'run' : 'runs'}`,
			ws.removedRuns && `${ws.removedRuns} ${ws.removedRuns === 1 ? 'run' : 'runs'} removed`,
			ws.newMetrics && `${ws.newMetrics} new ${ws.newMetrics === 1 ? 'metric' : 'metrics'}`
		]
			.filter(Boolean)
			.join(' · ')
	);

	/** The runs shown are a search's or a filter's, not all of the project's. */
	const narrowed = $derived(!!ws.page.query.search || ws.page.query.filters.length > 0);

	const noMatch = $derived.by(() => {
		const { search, filters } = ws.page.query;
		if (!filters.length) return `No run’s name or id contains “${search}”.`;
		const kindOf = (key: string) => ws.meta.columns.find((c) => c.key === key)?.kind;
		const where = describeFilters(filters, kindOf);
		if (!search) return `No run matches ${where}.`;
		return `No run matching “${search}” has ${where}.`;
	});

	// The project's record with the counts the stream keeps current: the dialogs explain
	// what an active run prevents.
	const projectInfo = $derived({
		entity: ws.meta.entity,
		name: ws.meta.project,
		description: data.project?.description ?? null,
		created_at: data.project?.created_at ?? 0,
		updated_at: data.project?.updated_at ?? 0,
		column_count: ws.meta.columns.length,
		run_count: ws.page.projectTotal,
		active_run_count: ws.activeRuns
	});
</script>

<svelte:head>
	<title>{ws.meta.project} · {data.shell.orgName ?? ws.meta.entity}</title>
</svelte:head>

<svelte:window {onkeydown} />

<Page width="full" data-sveltekit-reset={false} style="--name-w: {nameW}px">
	<PageHeader title={ws.meta.project} subtitleTitle={projectInfo.description ?? undefined}>
		{#snippet subtitle()}
			{#if projectInfo.description}
				<span class="description">{projectInfo.description}</span>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if ws.newRuns > 0 || ws.removedRuns > 0 || ws.newMetrics > 0}
				<Button
					variant="secondary"
					size="sm"
					class="new-runs"
					onclick={showNewRuns}
					disabled={refreshing}
				>
					{updatesLabel} · {refreshing ? 'Loading…' : 'Show'}
				</Button>
			{/if}
			<LiveStatus />
		{/snippet}
		{#snippet toolbar()}
			<Tabs.Root value={view} onValueChange={(v) => setView(v as View)}>
				<!-- No counts, so the toggle keeps its width from project to project. -->
				<Tabs.List aria-label="View">
					<Tabs.Trigger value="table" title="Table (T)">Table</Tabs.Trigger>
					<Tabs.Trigger value="charts" title="Charts (C)">Charts</Tabs.Trigger>
				</Tabs.List>
			</Tabs.Root>
			<Separator orientation="vertical" class="mx-1.5 h-5! data-vertical:self-center" />

			{#if view === 'table' && ws.selectedRows.length}
				<span class="selection-count mr-1 text-[13px] font-medium tabular-nums" role="status">
					{ws.selectedRows.length} selected
				</span>
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
					title="Clear selection (Esc)"
					aria-label="Clear selection"
					onclick={() => ws.selected.clear()}
				>
					<XIcon />
				</Button>
			{:else}
				<RunSearch />
				<RunFilters />

				{#if view === 'table'}
					<ColumnsMenu onjump={(key) => table?.scrollToColumn(key)} />
				{:else}
					<PlotSettings />
				{/if}
			{/if}

			<span class="flex-1"></span>
			{#if view === 'charts' && focusLabel}
				<Badge
					variant="secondary"
					class="focus-label max-w-[46%] truncate text-muted-foreground"
					title={focusLabel}
				>
					{focusLabel}
				</Badge>
			{/if}
		{/snippet}
	</PageHeader>

	<main class="relative min-h-0 flex-1">
		<RunsTable bind:this={table} mode={view} {nameW} />
		<ChartsPane
			shown={view === 'charts'}
			active={view === 'charts' && fullscreen == null}
			{panelHref}
			onfullscreen={openPanel}
		/>
		{#if ws.page.total === 0 && narrowed}
			<EmptyState
				variant="onboarding"
				class="empty absolute inset-0 z-4 bg-background"
				title="No matching runs"
			>
				{#snippet lead()}{noMatch}{/snippet}
				<Button
					class="mt-4"
					variant="outline"
					href={hrefWith({ q: null, filters: null, page: null })}
				>
					Show every run
				</Button>
			</EmptyState>
		{:else if ws.page.total === 0}
			<EmptyState
				variant="onboarding"
				class="empty absolute inset-0 z-4 bg-background"
				title="No runs yet"
			>
				{#snippet lead()}
					The SDK needs a wandb key: <a
						class="text-link underline-offset-3 hover:underline"
						href={resolve('/(app)/account')}>create one</a
					>.
				{/snippet}
				<pre
					class="mt-4 overflow-x-auto rounded-lg bg-muted px-4 py-3.5 font-mono text-[13px] leading-relaxed"><code
						>import wandb

run = wandb.init(entity="{ws.meta.entity}", project="{ws.meta.project}")
run.log(&lbrace;"loss": 0.5&rbrace;)</code
					></pre>
			</EmptyState>
		{:else}
			<Pager />
			<!-- On the name column's edge, above the table and the charts beside it. -->
			<div class="pointer-events-none absolute inset-y-0 left-0 z-5" style:width="{nameW}px">
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
<EditRunDialog />
<TagRunsDialog />
<MoveRunsDialog />
<DeleteRunsDialog />
