<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import PanelRightIcon from '@lucide/svelte/icons/panel-right';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import TagIcon from '@lucide/svelte/icons/tag';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import XIcon from '@lucide/svelte/icons/x';
	import { onMount, untrack } from 'svelte';
	import { MediaQuery } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import ChartsPane from '#lib/components/ChartsPane.svelte';
	import CursorPanel from '#lib/components/CursorPanel.svelte';
	import DeleteRunsDialog from '#lib/components/DeleteRunsDialog.svelte';
	import EditRunDialog from '#lib/components/EditRunDialog.svelte';
	import FullscreenPanel from '#lib/components/FullscreenPanel.svelte';
	import LiveStatus from '#lib/components/LiveStatus.svelte';
	import MoveRunsDialog from '#lib/components/MoveRunsDialog.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import PlotSettings from '#lib/components/PlotSettings.svelte';
	import RunFiles from '#lib/components/run/RunFiles.svelte';
	import RunLogs from '#lib/components/run/RunLogs.svelte';
	import RunOverview from '#lib/components/run/RunOverview.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import TagRunsDialog from '#lib/components/TagRunsDialog.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { Separator } from '#lib/components/ui/separator/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { projectUrl } from '#lib/data/cache.ts';
	import { LiveConnection, type ProjectEvent } from '#lib/data/live.ts';
	import { runWorkspace } from '#lib/data/run.ts';
	import { LIVE_STATES } from '#lib/types.ts';
	import { entityHref, projectHref, runHref, withParam } from '#lib/workspace/url.ts';
	import { Workspace, setWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// The run as a workspace of one, so its panels are the project's panels.
	const workspace = $derived(runWorkspace(data.run, data.history, data.system, data.now));
	const ws = new Workspace(
		() => workspace.meta,
		() => workspace.page
	);
	setWorkspace(ws);

	// The project's panel arrangement applies here too.
	$effect(() => {
		void [ws.meta.entity, ws.meta.project];
		untrack(() => ws.restoreLayout());
	});

	function onproject(e: ProjectEvent) {
		// Renamed or deleted under us: the project's page knows where it went.
		if (e.deleted) void goto(entityHref(data.run.entity), { replace: true });
		else
			void goto(
				projectHref(e.entity_renamed_to ?? data.run.entity, e.renamed_to ?? data.run.project)
			);
	}
	const live = new LiveConnection(ws, onproject);
	$effect(() => {
		live.connect(ws.meta, ws.page);
		return () => live.close();
	});

	const runState = $derived(ws.stateOf(0));
	const name = $derived(ws.nameOf(0));

	type Tab = 'charts' | 'logs' | 'files';
	const TABS: { value: Tab; label: string }[] = [
		{ value: 'charts', label: 'Charts' },
		{ value: 'logs', label: 'Logs' },
		{ value: 'files', label: 'Files' }
	];
	// In the URL, so a tab can be linked to; switching never re-runs the load.
	let tabOverride = $state<Tab | null>(null);
	const urlTab = $derived<Tab>(
		TABS.find((t) => t.value === page.url.searchParams.get('tab'))?.value ?? 'charts'
	);
	const tab = $derived(tabOverride ?? urlTab);

	function setTab(t: Tab) {
		if (t === tab) return;
		tabOverride = t;
		ws.cursor = null;
		void goto(withParam(page.url, 'tab', t === 'charts' ? null : t), {
			replace: true,
			reset: false
		}).finally(() => (tabOverride = null));
	}

	// The overview: a sidebar beside every tab. Open by default on wide screens, where the choice
	// is remembered; on narrow ones it opens over the tab, for this visit only.
	const SIDEBAR = 'flock:run-sidebar';
	const wide = new MediaQuery('(min-width: 64rem)', true);
	let mounted = $state(false);
	let widePref = $state(true);
	let narrowOpen = $state(false);
	const sidebar = $derived(wide.current ? widePref : narrowOpen);

	onMount(() => {
		try {
			const saved = localStorage.getItem(SIDEBAR);
			// An old overview link opens it, whatever was chosen before (see below).
			if (saved != null && page.url.searchParams.get('tab') !== 'overview')
				widePref = saved !== 'closed';
		} catch {
			// Open, the default.
		}
		mounted = true;
	});

	function setSidebar(open: boolean, remember = true) {
		if (!wide.current) {
			narrowOpen = open;
			return;
		}
		widePref = open;
		if (!remember) return;
		try {
			localStorage.setItem(SIDEBAR, open ? 'open' : 'closed');
		} catch {
			// For this visit only.
		}
	}

	// The overview used to be a tab: its links land on the charts, with the sidebar open.
	$effect(() => {
		if (page.url.searchParams.get('tab') !== 'overview') return;
		untrack(() => {
			setSidebar(true, false);
			void goto(withParam(page.url, 'tab', null), { replace: true, reset: false });
		});
	});

	// Full-screen panels, as on the project's page: `?panel=<metric>`.
	const fullscreen = $derived.by(() => {
		const key = page.url.searchParams.get('panel');
		const i = key ? ws.meta.metrics.findIndex((m) => m.key === key) : -1;
		return i === -1 || tab !== 'charts' ? null : i;
	});
	const panelHref = (m: number) => withParam(page.url, 'panel', ws.meta.metrics[m].key);
	const neighbours = $derived.by(() => {
		if (fullscreen == null) return { prev: null, next: null };
		const flat = ws.sections.flatMap((s) => s.metrics);
		const i = flat.indexOf(fullscreen);
		return { prev: i > 0 ? flat[i - 1] : null, next: i < flat.length - 1 ? flat[i + 1] : null };
	});

	function openPanel(m: number, e?: MouseEvent) {
		if (e && (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0)) return;
		e?.preventDefault();
		ws.cursor = null;
		void goto(panelHref(m), { reset: false });
	}

	function closePanel(e?: MouseEvent) {
		e?.preventDefault();
		ws.cursor = null;
		ws.hover = null;
		void goto(withParam(page.url, 'panel', null), { replace: true, reset: false });
	}

	const base = $derived(
		`${projectUrl(data.run.entity, data.run.project)}/runs/${encodeURIComponent(data.run.id)}`
	);
</script>

<svelte:head>
	<title>{name} · {data.run.project}</title>
</svelte:head>

<div class="flex min-h-0 flex-1 flex-col">
	<PageHeader title={name}>
		{#snippet badges()}
			<DropdownMenu.Root>
				<DropdownMenu.Trigger>
					{#snippet child({ props })}
						<Button {...props} variant="ghost" size="icon-sm" aria-label="Run actions">
							<EllipsisIcon />
						</Button>
					{/snippet}
				</DropdownMenu.Trigger>
				<DropdownMenu.Content align="start" class="w-48">
					<DropdownMenu.Item onSelect={() => ws.openDialog('edit', 0)}>
						<PencilIcon /> Edit run…
					</DropdownMenu.Item>
					<DropdownMenu.Item onSelect={() => ws.openDialog('tag', 0)}>
						<TagIcon /> Tags…
					</DropdownMenu.Item>
					<DropdownMenu.Item onSelect={() => ws.openDialog('move', 0)}>
						<FolderInputIcon /> Move to project…
					</DropdownMenu.Item>
					<DropdownMenu.Separator />
					<DropdownMenu.Item variant="destructive" onSelect={() => ws.openDialog('delete', 0)}>
						<Trash2Icon /> Delete run…
					</DropdownMenu.Item>
				</DropdownMenu.Content>
			</DropdownMenu.Root>
			<StateBadge state={runState} />
		{/snippet}
		{#snippet actions()}
			<LiveStatus />
			<Button
				variant="ghost"
				size="sm"
				class="aria-pressed:bg-muted aria-pressed:text-foreground"
				aria-pressed={sidebar}
				aria-controls="run-overview"
				title={sidebar ? 'Hide the overview' : 'Show the overview'}
				onclick={() => setSidebar(!sidebar)}
			>
				<PanelRightIcon />
				<span class="max-sm:sr-only">Overview</span>
			</Button>
		{/snippet}
		{#snippet toolbar()}
			<Tabs.Root value={tab} onValueChange={(v) => setTab(v as Tab)}>
				<Tabs.List aria-label="Run">
					{#each TABS as t (t.value)}
						<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
					{/each}
				</Tabs.List>
			</Tabs.Root>
			{#if tab === 'charts'}
				<Separator orientation="vertical" class="mx-1.5 h-5! data-vertical:self-center" />
				<PlotSettings />
			{/if}
		{/snippet}
	</PageHeader>

	<div class="relative flex min-h-0 flex-1">
		<main class="relative min-h-0 min-w-0 flex-1">
			{#if tab === 'charts'}
				{#if ws.meta.metrics.length}
					<ChartsPane
						shown
						active={fullscreen == null}
						beside={false}
						{panelHref}
						onfullscreen={openPanel}
					/>
				{:else}
					<p class="px-5 py-6 text-muted-foreground">This run hasn’t logged any metrics.</p>
				{/if}
			{:else if tab === 'logs'}
				{#key data.run.id}
					<RunLogs
						url="{base}/logs"
						total={data.run.log_line_count}
						live={LIVE_STATES.has(runState)}
						start={data.run.created_at}
						{name}
					/>
				{/key}
			{:else}
				{#key data.run.id}
					<RunFiles url="{base}/files" now={data.now} />
				{/key}
			{/if}
		</main>
		<!-- Before hydration, CSS stands in for the defaults: open on wide screens, closed on narrow. -->
		{#if sidebar || !mounted}
			<aside
				id="run-overview"
				aria-label="Overview"
				class={[
					'flex w-[22rem] flex-none flex-col border-l bg-background',
					'max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-20 max-lg:w-[min(22rem,100%)] max-lg:shadow-xl',
					!mounted && 'max-lg:hidden'
				]}
			>
				<div
					class="flex h-10 flex-none items-center justify-between border-b border-divider pr-2 pl-4"
				>
					<h2 class="text-[13px] font-medium">Overview</h2>
					<Button
						variant="ghost"
						size="icon-xs"
						aria-label="Hide the overview"
						title="Hide the overview"
						onclick={() => setSidebar(false)}
					>
						<XIcon />
					</Button>
				</div>
				<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain">
					<RunOverview
						run={data.run}
						{runState}
						owner={data.owner}
						currency={data.currency}
						now={data.now}
					/>
				</div>
			</aside>
		{/if}
	</div>
</div>

{#if fullscreen != null}
	<FullscreenPanel
		metric={fullscreen}
		closeHref={withParam(page.url, 'panel', null)}
		prevHref={neighbours.prev != null ? panelHref(neighbours.prev) : null}
		nextHref={neighbours.next != null ? panelHref(neighbours.next) : null}
		onclose={closePanel}
	/>
{/if}

<CursorPanel />
<EditRunDialog
	gpu={{ type: data.run.gpu_type, count: data.run.gpu_count, detected: data.run.gpu_detected }}
/>
<TagRunsDialog />
<MoveRunsDialog leave={(to) => runHref(data.run.entity, to, data.run.id)} />
<DeleteRunsDialog leave={() => projectHref(data.run.entity, data.run.project)} />
