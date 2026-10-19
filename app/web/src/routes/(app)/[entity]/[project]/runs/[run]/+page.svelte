<script lang="ts">
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import ChartsPane from '#lib/components/ChartsPane.svelte';
	import CursorPanel from '#lib/components/CursorPanel.svelte';
	import FullscreenPanel from '#lib/components/FullscreenPanel.svelte';
	import LiveStatus from '#lib/components/LiveStatus.svelte';
	import PlotSettings from '#lib/components/PlotSettings.svelte';
	import RunFiles from '#lib/components/run/RunFiles.svelte';
	import RunLogs from '#lib/components/run/RunLogs.svelte';
	import RunOverview from '#lib/components/run/RunOverview.svelte';
	import StateBadge from '#lib/components/StateBadge.svelte';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { projectUrl } from '#lib/data/cache.ts';
	import { LiveConnection, type ProjectEvent } from '#lib/data/live.ts';
	import { runWorkspace } from '#lib/data/run.ts';
	import { LIVE_STATES } from '#lib/types.ts';
	import { entityHref, projectHref, withParam } from '#lib/workspace/url.ts';
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

	type Tab = 'charts' | 'overview' | 'logs' | 'files';
	const TABS: { value: Tab; label: string }[] = [
		{ value: 'charts', label: 'Charts' },
		{ value: 'overview', label: 'Overview' },
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
	<div class="flex-none px-5 pt-4 pb-3">
		<a
			class="inline-flex items-center gap-1.5 text-[13px] text-muted-foreground hover:text-foreground"
			href={projectHref(data.run.entity, data.run.project)}
		>
			<ArrowLeftIcon class="size-3.5" />
			{data.run.project}
		</a>
		<div class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
			<h1
				class="min-w-0 truncate font-serif text-[34px] leading-[1.15] font-normal tracking-[-0.02em]"
			>
				{name}
			</h1>
			<StateBadge state={runState} />
			{#if name !== data.run.id}
				<span class="font-mono text-[13px] text-muted-foreground">{data.run.id}</span>
			{/if}
			<span class="ml-auto"><LiveStatus /></span>
		</div>
	</div>

	<div class="flex h-12 flex-none items-center gap-2 border-b px-5">
		<Tabs.Root value={tab} onValueChange={(v) => setTab(v as Tab)}>
			<Tabs.List aria-label="Run">
				{#each TABS as t (t.value)}
					<Tabs.Trigger value={t.value}>
						{t.label}
						{#if t.value === 'charts'}
							<span class="font-normal text-muted-foreground">{ws.meta.metrics.length}</span>
						{:else if t.value === 'logs' && data.run.log_line_count}
							<span class="font-normal text-muted-foreground">
								{data.run.log_line_count.toLocaleString('en-US')}
							</span>
						{/if}
					</Tabs.Trigger>
				{/each}
			</Tabs.List>
		</Tabs.Root>
		{#if tab === 'charts'}
			<span class="mx-1.5 h-5 w-px bg-border" aria-hidden="true"></span>
			<PlotSettings />
		{/if}
	</div>

	<main class="relative min-h-0 flex-1">
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
		{:else if tab === 'overview'}
			<div class="absolute inset-0 overflow-y-auto">
				<RunOverview
					run={data.run}
					{runState}
					owner={data.owner}
					currency={data.currency}
					now={data.now}
				/>
			</div>
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
