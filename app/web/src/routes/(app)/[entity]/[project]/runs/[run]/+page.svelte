<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import GitCompareArrowsIcon from '@lucide/svelte/icons/git-compare-arrows';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import TagIcon from '@lucide/svelte/icons/tag';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { untrack } from 'svelte';
	import { goto, invalidateAll, replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import ChartsPane from '#lib/components/ChartsPane.svelte';
	import DetailPage from '#lib/components/DetailPage.svelte';
	import DeleteRunsDialog from '#lib/components/DeleteRunsDialog.svelte';
	import EditRunDialog from '#lib/components/EditRunDialog.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import FullscreenPanel from '#lib/components/FullscreenPanel.svelte';
	import PanelEditor from '#lib/components/PanelEditor.svelte';
	import MoveRunsDialog from '#lib/components/MoveRunsDialog.svelte';
	import PlotSettings from '#lib/components/PlotSettings.svelte';
	import RunFiles from '#lib/components/run/RunFiles.svelte';
	import RunLogs from '#lib/components/run/RunLogs.svelte';
	import RunOverview from '#lib/components/run/RunOverview.svelte';
	import RunResults from '#lib/components/run/RunResults.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import TagsEditor from '#lib/components/TagsEditor.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { projectUrl } from '#lib/data/cache.ts';
	import { LiveConnection, type ProjectEvent } from '#lib/data/live.ts';
	import { runWorkspace } from '#lib/data/run.ts';
	import { keyboard } from '#lib/keys.svelte.ts';
	import { compareHref, itemsViewOf, withItemsView } from '#lib/evals.ts';
	import { duration } from '#lib/utils.ts';
	import { pane } from '#lib/components/pane.svelte.ts';
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
		untrack(() => ws.restoreLayout({ hidden: false }));
	});

	/** Outside the org: read-only, with no run actions (D116). */
	const readonly = $derived(data.shell.access === 'public');

	function onproject(e: ProjectEvent) {
		// Renamed or deleted under us: the project's page knows where it went. Made private while
		// an outsider watched: reading it again sends them to sign in, or to the 404 page.
		if (e.visibility === 'private') void invalidateAll();
		else if (e.deleted) void goto(entityHref(data.run.entity), { replace: true });
		else void goto(projectHref(data.run.entity, e.renamed_to ?? data.run.project));
	}
	const live = new LiveConnection(ws, onproject);
	$effect(() => {
		live.connect(ws.meta, ws.page);
		return () => live.close();
	});

	const runState = $derived(ws.stateOf(0));
	const name = $derived(ws.nameOf(0));

	type Tab = 'charts' | 'results' | 'logs' | 'files';
	// Results for every eval run, so eval runs all have the same tabs, and for any other run
	// with a results file (025 D184). A run with Results opens on it; any other on its charts.
	const hasResults = $derived(data.run.job_type === 'eval' || data.results != null);
	const TABS: { value: Tab; label: string }[] = $derived([
		...(hasResults ? [{ value: 'results' as const, label: 'Results' }] : []),
		{ value: 'charts', label: 'Charts' },
		{ value: 'logs', label: 'Logs' },
		{ value: 'files', label: 'Files' }
	]);
	const defaultTab = $derived<Tab>(hasResults ? 'results' : 'charts');
	// In the URL, so a tab can be linked to; switching never re-runs the load.
	let tabOverride = $state<Tab | null>(null);
	const urlTab = $derived<Tab>(
		TABS.find((t) => t.value === page.url.searchParams.get('tab'))?.value ?? defaultTab
	);
	const tab = $derived(tabOverride ?? urlTab);

	function setTab(t: Tab) {
		if (t === tab) return;
		tabOverride = t;
		ws.cursor = null;
		void goto(withParam(page.url, 'tab', t === defaultTab ? null : t), {
			replace: true,
			reset: false
		}).finally(() => (tabOverride = null));
	}

	// The overview used to be a tab, then a sidebar: its links land on the first tab, with the
	// properties pane open.
	$effect(() => {
		if (page.url.searchParams.get('tab') !== 'overview') return;
		untrack(() => {
			pane.open = true;
			void goto(withParam(page.url, 'tab', null), { replace: true, reset: false });
		});
	});

	// Against another run, any project's: the compare page, asking for the other run.
	function compare() {
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- compareHref resolves it
		void goto(compareHref(data.run.entity, data.run.project, data.run.id));
	}

	// The palette's Compare lists this project's runs first.
	$effect(() => {
		keyboard.compare = { entity: data.run.entity, project: data.run.project };
		return () => {
			keyboard.compare = null;
		};
	});

	// Full-screen panels, as on the project's page: `?panel=<panel id>`.
	const fullscreen = $derived.by(() => {
		const id = page.url.searchParams.get('panel');
		const i = id ? ws.findPanel(id) : -1;
		return i === -1 || tab !== 'charts' ? null : i;
	});
	const panelHref = (p: number) => withParam(page.url, 'panel', ws.panelDefs[p].id);
	const neighbours = $derived.by(() => {
		if (fullscreen == null) return { prev: null, next: null };
		const flat = ws.sections.flatMap((s) => s.panels);
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

	/** The view bar's place for the log's controls, while Logs is open. */
	let logBar = $state<HTMLElement | null>(null);

	const base = $derived(
		`${projectUrl(data.run.entity, data.run.project)}/runs/${encodeURIComponent(data.run.id)}`
	);
</script>

<svelte:head>
	<title>{name} · {data.run.project}</title>
</svelte:head>

<!-- Members' actions on the run. -->
{#snippet runMenu()}
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
			<DropdownMenu.Sub>
				<DropdownMenu.SubTrigger><TagIcon /> Tags</DropdownMenu.SubTrigger>
				<DropdownMenu.SubContent class="w-72 p-0" sideOffset={6}>
					<TagsEditor ids={[ws.page.runs[0].id]} />
				</DropdownMenu.SubContent>
			</DropdownMenu.Sub>
			<DropdownMenu.Item onSelect={() => ws.openDialog('move', 0)}>
				<FolderInputIcon /> Move to project…
			</DropdownMenu.Item>
			<DropdownMenu.Item onSelect={() => compare()}>
				<GitCompareArrowsIcon /> Compare with…
			</DropdownMenu.Item>
			<DropdownMenu.Separator />
			<DropdownMenu.Item variant="destructive" onSelect={() => ws.openDialog('delete', 0)}>
				<Trash2Icon /> Delete run…
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/snippet}

<DetailPage title={name} org tabs={TABS} {tab} ontab={setTab} tabsLabel="Run" fill>
	{#snippet badges()}
		{#if !readonly}{@render runMenu()}{/if}
	{/snippet}
	<!-- The run's state at the row's end, apart from its menu: two marks side by side read as
	     one cluster. Phones show it in the Details row instead, so the name keeps the width. -->
	{#snippet actions()}
		<span class="hidden sm:contents"><StateBadge state={runState} /></span>
	{/snippet}
	{#snippet controls()}
		{#if tab === 'charts'}
			<PlotSettings />
		{:else if tab === 'results' && !readonly}
			<Button
				variant="quiet"
				size="bar"
				data-sveltekit-preload-data="tap"
				href={compareHref(data.run.entity, data.run.project, data.run.id)}
			>
				<GitCompareArrowsIcon /> Compare with…
			</Button>
		{:else if tab === 'logs'}
			<!-- The log's own controls land here. -->
			<div class="flex items-center gap-0.5" bind:this={logBar}></div>
		{/if}
	{/snippet}
	{#snippet properties()}
		<RunOverview
			run={data.run}
			{runState}
			owner={data.owner}
			currency={data.currency}
			models={data.models}
			now={data.now}
			onlogs={tab === 'logs' ? undefined : () => setTab('logs')}
		/>
	{/snippet}
	{#snippet summary()}
		<StateBadge state={runState} />
		{#if data.run.runtime}<span>· {duration(data.run.runtime)}</span>{/if}
	{/snippet}

	{#if tab === 'charts'}
		{#if ws.meta.metrics.length}
			<ChartsPane
				shown
				active={fullscreen == null}
				beside={false}
				pack={false}
				{panelHref}
				onfullscreen={openPanel}
			/>
		{:else}
			<!-- A live run may still log some; an ended one never will. -->
			{#if LIVE_STATES.has(runState)}
				<EmptyState variant="onboarding" title="No metrics yet">
					{#snippet lead()}Charts appear once the run logs a metric.{/snippet}
				</EmptyState>
			{:else}
				<EmptyState variant="onboarding" title="No metrics logged" />
			{/if}
		{/if}
	{:else if tab === 'results'}
		{#if data.results}
			{#key data.run.id}
				<RunResults
					run={data.run}
					results={data.results}
					models={data.models}
					view={itemsViewOf(page.url.searchParams)}
					onview={(view) => replaceState(withItemsView(page.url, view), page.state)}
				/>
			{/key}
		{:else}
			<EmptyState variant="onboarding" title="No results yet">
				{#snippet lead()}
					Runs logged with <code class="font-mono text-[0.95em]"
						>flock eval run --wandb-project</code
					>
					upload their results and predictions.
				{/snippet}
			</EmptyState>
		{/if}
	{:else if tab === 'logs'}
		{#key data.run.id}
			<RunLogs
				url="{base}/logs"
				total={data.run.log_line_count}
				live={LIVE_STATES.has(runState)}
				start={data.run.created_at}
				{name}
				bar={logBar}
			/>
		{/key}
	{:else}
		{#key data.run.id}
			<RunFiles url="{base}/files" now={data.now} />
		{/key}
	{/if}
</DetailPage>

{#if fullscreen != null}
	<FullscreenPanel
		panel={fullscreen}
		closeHref={withParam(page.url, 'panel', null)}
		prevHref={neighbours.prev != null ? panelHref(neighbours.prev) : null}
		nextHref={neighbours.next != null ? panelHref(neighbours.next) : null}
		onclose={closePanel}
	/>
{/if}

<PanelEditor />
{#if !readonly}
	<EditRunDialog
		gpu={{ type: data.run.gpu_type, count: data.run.gpu_count, detected: data.run.gpu_detected }}
	/>
	<MoveRunsDialog leave={(to) => runHref(data.run.entity, to, data.run.id)} />
	<DeleteRunsDialog leave={() => projectHref(data.run.entity, data.run.project)} />
{/if}
