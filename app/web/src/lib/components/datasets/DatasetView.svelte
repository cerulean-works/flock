<script lang="ts">
	import FolderOpenIcon from '@lucide/svelte/icons/folder-open';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { DATASETS_BUCKET, datasetCall, datasetUri, datasetsApi, nameOf } from '#lib/datasets.ts';
	import { objectHref } from '#lib/storage.ts';
	import type { DatasetInfo } from '#lib/types.ts';
	import { bytes, dateTime } from '#lib/utils.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import CopyButton from '../CopyButton.svelte';
	import DeleteDatasetDialog from './DeleteDatasetDialog.svelte';
	import ReadmeTab from './ReadmeTab.svelte';
	import RenameDialog from './RenameDialog.svelte';
	import RowsTab from './RowsTab.svelte';
	import SchemaTab from './SchemaTab.svelte';
	import VersionPicker from './VersionPicker.svelte';
	import VersionsTab from './VersionsTab.svelte';

	interface Props {
		org: string;
		path: string;
		/** The dataset at the version shown; null when Lance couldn't read it (`failed` says why). */
		info: DatasetInfo | null;
		failed: string | null;
		/** The `?version=` asked for (a number or a tag), null for the latest. */
		requested: string | null;
		now: number;
	}

	let { org, path, info, failed, requested, now }: Props = $props();

	type Tab = 'readme' | 'rows' | 'schema' | 'versions';
	const TABS: { value: Tab; label: string }[] = [
		{ value: 'readme', label: 'README' },
		{ value: 'rows', label: 'Rows' },
		{ value: 'schema', label: 'Schema' },
		{ value: 'versions', label: 'Versions' }
	];
	// In the URL, so a tab can be linked to; switching never re-runs the page's load.
	let tabOverride = $state<Tab | null>(null);
	const urlTab = $derived<Tab>(
		TABS.find((t) => t.value === page.url.searchParams.get('tab'))?.value ?? 'readme'
	);
	const tab = $derived(tabOverride ?? urlTab);

	function setTab(t: Tab) {
		if (t === tab) return;
		tabOverride = t;
		let to = withParam(page.url, 'tab', t === 'readme' ? null : t);
		to = withParam(new URL(to, page.url.origin), 'offset', null);
		void goto(to, { replace: true, reset: false }).finally(() => (tabOverride = null));
	}

	/** Where to see another version: `?version=`, the tab kept. */
	const versionHref = (version: string | null) => withParam(page.url, 'version', version);

	const newer = $derived(
		info && info.latest_version !== null && info.latest_version > info.version
			? info.latest_version
			: null
	);
	// Tags, read again after one changes in the Versions tab. The version shown stays as it is,
	// even if a tag it was opened by moved (D96).
	let freshTags = $state<DatasetInfo['tags'] | null>(null);
	const allTags = $derived(freshTags ?? info?.tags ?? []);
	const tags = $derived(allTags.filter((t) => t.version === info?.version));

	async function refreshTags() {
		if (!info) return;
		try {
			const got = await datasetCall<DatasetInfo>(
				datasetsApi(org, 'info', { path, version: info.version })
			);
			freshTags = got.tags;
		} catch {
			// The header keeps the tags it had; the Versions tab shows the change.
		}
	}

	let renaming = $state(false);
	let deleting = $state(false);
</script>

<div class="flex flex-wrap items-baseline gap-x-3 gap-y-2 pt-3">
	<h1 class="min-w-0 truncate text-[34px] leading-[1.15] tracking-[-0.025em]" title={path}>
		{nameOf(path)}
	</h1>
	{#if info}
		<VersionPicker {org} {path} version={info.version} {requested} tags={allTags} {versionHref} />
	{/if}
</div>

<div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
	<p class="flex min-w-0 items-center gap-1 font-mono text-[13px] text-muted-foreground">
		<span class="truncate">{datasetUri(path)}</span>
		<CopyButton text={datasetUri(path)} label="Copy S3 URI" />
	</p>
	<div class="ml-auto flex items-center gap-1">
		<Button
			variant="ghost"
			size="sm"
			class="text-muted-foreground"
			href={objectHref(org, DATASETS_BUCKET, `${path}/`)}
		>
			<FolderOpenIcon /> Browse files
		</Button>
		<Button
			variant="ghost"
			size="sm"
			class="text-muted-foreground"
			onclick={() => (renaming = true)}
		>
			<PencilIcon /> Rename
		</Button>
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

{#if info}
	<dl class="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[13px]" aria-label="This version">
		<div class="flex gap-1.5">
			<dt class="text-muted-foreground">Rows</dt>
			<dd class="tabular-nums">{info.rows.toLocaleString('en-US')}</dd>
		</div>
		<div class="flex gap-1.5">
			<dt class="text-muted-foreground">Columns</dt>
			<dd class="tabular-nums">{info.columns.toLocaleString('en-US')}</dd>
		</div>
		<div class="flex gap-1.5">
			<dt class="text-muted-foreground">Fragments</dt>
			<dd class="tabular-nums">{info.fragments.toLocaleString('en-US')}</dd>
		</div>
		<div class="flex gap-1.5">
			<dt class="text-muted-foreground">Data size</dt>
			<dd class="tabular-nums">{bytes(info.data_size)}</dd>
		</div>
		<div class="flex gap-1.5">
			<dt class="text-muted-foreground">Written</dt>
			<dd class="tabular-nums">{dateTime(info.timestamp)}</dd>
		</div>
		{#if tags.length}
			<div class="flex gap-1.5">
				<dt class="text-muted-foreground">{tags.length === 1 ? 'Tag' : 'Tags'}</dt>
				<dd>{tags.map((t) => t.name).join(', ')}</dd>
			</div>
		{/if}
	</dl>
	{#if newer !== null}
		<p class="mt-3 text-[13px] text-muted-foreground" role="status">
			New version available.
			<a class="text-link underline-offset-3 hover:underline" href={versionHref(String(newer))}>
				View version {newer}
			</a>
		</p>
	{/if}
{/if}

<div class="mt-6">
	<Tabs.Root value={tab} onValueChange={(v) => setTab(v as Tab)}>
		<Tabs.List aria-label="Dataset">
			{#each TABS as t (t.value)}
				<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
			{/each}
		</Tabs.List>
	</Tabs.Root>
</div>

<div class="mt-4">
	{#if tab === 'readme'}
		<ReadmeTab {org} {path} />
	{:else if failed || !info}
		<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">
			{failed ?? 'This dataset couldn’t be read.'}
		</p>
	{:else if tab === 'rows'}
		{#key info.version}
			<RowsTab {org} {path} {info} />
		{/key}
	{:else if tab === 'schema'}
		<SchemaTab {info} />
	{:else}
		<VersionsTab {org} {path} shown={info.version} {versionHref} ontags={refreshTags} {now} />
	{/if}
</div>

<RenameDialog bind:open={renaming} {org} {path} />
<DeleteDatasetDialog bind:open={deleting} {org} {path} />
