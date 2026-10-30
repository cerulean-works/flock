<script lang="ts">
	import DownloadIcon from '@lucide/svelte/icons/download';
	import FolderOpenIcon from '@lucide/svelte/icons/folder-open';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { folderHref } from '#lib/models.ts';
	import type { ModelInfo, ModelVersionDetail } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import DeleteModelDialog from './DeleteModelDialog.svelte';
	import EvalsTab from './EvalsTab.svelte';
	import FilesTab from './FilesTab.svelte';
	import LoadDialog from './LoadDialog.svelte';
	import OverviewTab from './OverviewTab.svelte';
	import VersionPicker from './VersionPicker.svelte';
	import VersionsTab from './VersionsTab.svelte';

	interface Props {
		org: string;
		info: ModelInfo;
		/** The version shown; null while the model has none. */
		version: ModelVersionDetail | null;
		/** The `?version=` asked for (a number or a tag), null for the primary. */
		requested: string | null;
		now: number;
	}

	let { org, info, version, requested, now }: Props = $props();

	type Tab = 'overview' | 'versions' | 'evals' | 'files';
	const TABS: { value: Tab; label: string }[] = [
		{ value: 'overview', label: 'Overview' },
		{ value: 'versions', label: 'Versions' },
		{ value: 'evals', label: 'Evals' },
		{ value: 'files', label: 'Files' }
	];
	// In the URL, so a tab can be linked to; switching never re-runs the page's load.
	let tabOverride = $state<Tab | null>(null);
	const urlTab = $derived<Tab>(
		TABS.find((t) => t.value === page.url.searchParams.get('tab'))?.value ?? 'overview'
	);
	const tab = $derived(tabOverride ?? urlTab);

	function setTab(t: Tab) {
		if (t === tab) return;
		tabOverride = t;
		const to = withParam(page.url, 'tab', t === 'overview' ? null : t);
		void goto(to, { replace: true, reset: false }).finally(() => (tabOverride = null));
	}

	/** Where to see another version (a number or a tag; null: the primary), the tab kept. */
	const versionHref = (ref: string | null) => withParam(page.url, 'version', ref);

	/**
	 * Read the page again after a change. The version shown stays as it is, by number, even if
	 * the tag or the primary it was opened by moved.
	 */
	async function refresh() {
		if (!version) return invalidateAll();
		const pinned = versionHref(String(version.number));
		if (pinned === page.url.pathname + page.url.search) return invalidateAll();
		return goto(pinned, { replace: true, reset: false, invalidateAll: true });
	}

	const primarySetBy = $derived(
		version?.primary && info.primary_set_at !== null
			? `Made primary ${info.primary_set_by ? `by ${info.primary_set_by.name} ` : ''}${relativeTime(info.primary_set_at, now)}`
			: null
	);

	let loading = $state(false);
	let deleting = $state(false);
</script>

<div class="flex flex-wrap items-center gap-x-3 gap-y-2 pt-3">
	<h1 class="min-w-0 truncate text-[34px] leading-[1.15] tracking-[-0.025em]" title={info.model}>
		{info.name}
	</h1>
	{#if version}
		<VersionPicker
			{org}
			name={info.name}
			version={version.number}
			{requested}
			primary={info.primary}
			tags={info.tags}
			{versionHref}
		/>
		{#if version.primary}<Badge variant="secondary">Primary</Badge>{/if}
	{/if}
</div>

{#if version}
	<p class="mt-2 text-[13px] text-muted-foreground tabular-nums">
		{bytes(version.total_bytes)} ·
		<span title={dateTime(version.created_at)}
			>Pushed {version.created_by ? `by ${version.created_by.name} ` : ''}{relativeTime(
				version.created_at,
				now
			)}</span
		>
		{#if primarySetBy && info.primary_set_at !== null}
			· <span title={dateTime(info.primary_set_at)}>{primarySetBy}</span>
		{/if}
	</p>
{/if}

<div class="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2">
	<Tabs.Root value={tab} onValueChange={(v) => setTab(v as Tab)}>
		<Tabs.List aria-label="Model">
			{#each TABS as t (t.value)}
				<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
			{/each}
		</Tabs.List>
	</Tabs.Root>
	<div class="ml-auto flex flex-wrap items-center gap-1">
		{#if version}
			<Button
				variant="ghost"
				size="sm"
				class="text-muted-foreground"
				onclick={() => (loading = true)}
			>
				<DownloadIcon /> Load
			</Button>
			<Button
				variant="ghost"
				size="sm"
				class="text-muted-foreground"
				href={folderHref(org, version.prefix)}
			>
				<FolderOpenIcon /> Browse files
			</Button>
		{/if}
		<Button
			variant="ghost"
			size="sm"
			class="text-muted-foreground hover:text-destructive"
			onclick={() => (deleting = true)}
		>
			<Trash2Icon /> Delete
		</Button>
	</div>
</div>

<div class="mt-6">
	{#if tab === 'versions'}
		<VersionsTab
			{org}
			{info}
			shown={version?.number ?? null}
			{versionHref}
			onchange={refresh}
			{now}
		/>
	{:else if !version}
		<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">
			This model has no versions yet. A version appears once its upload finishes.
		</p>
	{:else if tab === 'overview'}
		{#key version.number}
			<OverviewTab {org} {version} />
		{/key}
	{:else if tab === 'evals'}
		{#key version.number}
			<EvalsTab {org} {version} />
		{/key}
	{:else}
		<FilesTab {org} {version} />
	{/if}
</div>

{#if version}
	<LoadDialog bind:open={loading} {org} name={info.name} number={version.number} />
{/if}
<DeleteModelDialog bind:open={deleting} {org} {info} />
