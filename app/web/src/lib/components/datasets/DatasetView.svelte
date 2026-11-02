<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import FolderOpenIcon from '@lucide/svelte/icons/folder-open';
	import GlobeIcon from '@lucide/svelte/icons/globe';
	import LockIcon from '@lucide/svelte/icons/lock';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import PlugIcon from '@lucide/svelte/icons/plug';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { SvelteSet } from 'svelte/reactivity';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import PublicBadge from '#lib/components/PublicBadge.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import * as Tabs from '#lib/components/ui/tabs/index.js';
	import { DATASETS_BUCKET, datasetCall, datasetsApi, nameOf } from '#lib/datasets.ts';
	import { objectHref } from '#lib/storage.ts';
	import type { Access, DatasetInfo, S3Config } from '#lib/types.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import AboutTab from './AboutTab.svelte';
	import ColumnsPicker from './ColumnsPicker.svelte';
	import DeleteDatasetDialog from './DeleteDatasetDialog.svelte';
	import RenameDialog from './RenameDialog.svelte';
	import RowsTab from './RowsTab.svelte';
	import S3AccessDialog from './S3AccessDialog.svelte';
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
		/** The S3 proxy, for the Access in S3 dialog. */
		s3: S3Config;
		/**
		 * What the viewer may do: admins also make it public or private; from outside the org
		 * (`public`) it's read-only, with nothing of S3 (D115).
		 */
		access?: Access;
		now: number;
	}

	let { org, path, info, failed, requested, s3, access = 'member', now }: Props = $props();

	const isPublic = $derived(!!info?.public);

	type Tab = 'about' | 'rows' | 'schema' | 'versions';
	const TABS: { value: Tab; label: string }[] = [
		{ value: 'about', label: 'About' },
		{ value: 'rows', label: 'Viewer' },
		{ value: 'schema', label: 'Schema' },
		{ value: 'versions', label: 'Versions' }
	];
	// In the URL, so a tab can be linked to; switching never re-runs the page's load.
	let tabOverride = $state<Tab | null>(null);
	const urlTab = $derived<Tab>(
		TABS.find((t) => t.value === page.url.searchParams.get('tab'))?.value ?? 'about'
	);
	const tab = $derived(tabOverride ?? urlTab);

	/** Columns the viewer hid, kept across versions; ones a version lacks are ignored. */
	const hidden = new SvelteSet<string>();

	function setTab(t: Tab) {
		if (t === tab) return;
		tabOverride = t;
		let to = withParam(page.url, 'tab', t === 'about' ? null : t);
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

	let accessing = $state(false);
	let renaming = $state(false);
	let deleting = $state(false);
	let publishing = $state(false);

	// Making it private again needs no confirmation.
	const unpublishing = new Mutation({
		toastErrors: true,
		success: () => `${nameOf(path)} is now private`
	});
	let unpublish = $state<HTMLFormElement>();
</script>

<div class="flex flex-wrap items-center gap-x-3 gap-y-2 pt-3">
	<h1 class="min-w-0 truncate text-[34px] leading-[1.15] tracking-[-0.025em]" title={path}>
		{nameOf(path)}
	</h1>
	{#if isPublic}<PublicBadge what="every version of it" />{/if}
	{#if info}
		<VersionPicker {org} {path} version={info.version} {requested} tags={allTags} {versionHref} />
	{/if}
</div>

{#if newer !== null}
	<p class="mt-3 text-[13px] text-muted-foreground" role="status">
		New version available.
		<a class="text-link underline-offset-3 hover:underline" href={versionHref(String(newer))}>
			View version {newer}
		</a>
	</p>
{/if}

<div class="mt-6 flex flex-wrap items-center gap-x-3 gap-y-2">
	<Tabs.Root value={tab} onValueChange={(v) => setTab(v as Tab)}>
		<Tabs.List aria-label="Dataset">
			{#each TABS as t (t.value)}
				<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
			{/each}
		</Tabs.List>
	</Tabs.Root>
	<div class="ml-auto flex flex-wrap items-center gap-1">
		{#if tab === 'rows' && info}
			<ColumnsPicker names={info.schema.map((f) => f.name)} {hidden} />
			<span class="mx-1 h-5 w-px bg-border" aria-hidden="true"></span>
		{/if}
		{#if access !== 'public'}
			<Button
				variant="ghost"
				size="sm"
				class="text-muted-foreground"
				onclick={() => (accessing = true)}
			>
				<PlugIcon /> Access in S3
			</Button>
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
		{/if}
		{#if access === 'admin' && info}
			<DropdownMenu.Root>
				<DropdownMenu.Trigger>
					{#snippet child({ props })}
						<Button
							{...props}
							variant="ghost"
							size="icon-sm"
							class="text-muted-foreground"
							aria-label="More dataset actions"
							disabled={unpublishing.pending}
						>
							<EllipsisIcon />
						</Button>
					{/snippet}
				</DropdownMenu.Trigger>
				<DropdownMenu.Content align="end" class="w-44">
					{#if isPublic}
						<DropdownMenu.Item onSelect={() => unpublish?.requestSubmit()}>
							<LockIcon /> Make private
						</DropdownMenu.Item>
					{:else}
						<DropdownMenu.Item onSelect={() => (publishing = true)}>
							<GlobeIcon /> Make public…
						</DropdownMenu.Item>
					{/if}
				</DropdownMenu.Content>
			</DropdownMenu.Root>
		{/if}
	</div>
</div>

<div class={tab === 'rows' ? 'mt-3' : 'mt-6'}>
	{#if tab === 'about'}
		<AboutTab {org} {path} {info} tags={tags.map((t) => t.name)} />
	{:else if failed || !info}
		<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">
			{failed ?? 'This dataset couldn’t be read.'}
		</p>
	{:else if tab === 'rows'}
		{#key info.version}
			<RowsTab {org} {path} {info} {hidden} />
		{/key}
	{:else if tab === 'schema'}
		<SchemaTab {info} />
	{:else}
		<VersionsTab
			{org}
			{path}
			shown={info.version}
			{versionHref}
			ontags={refreshTags}
			readonly={access === 'public'}
			{now}
		/>
	{/if}
</div>

{#if access !== 'public'}
	<S3AccessDialog bind:open={accessing} {path} {s3} />
	<RenameDialog bind:open={renaming} {org} {path} />
	<DeleteDatasetDialog bind:open={deleting} {org} {path} />
{/if}
{#if access === 'admin'}
	<ConfirmDialog
		bind:open={publishing}
		action="?/visibility"
		fields={{ public: 'true' }}
		title="Make {nameOf(path)} public?"
		description="Anyone with the link will be able to see every version of this dataset. Check that none of them hold secrets."
		confirm="Make public"
		pending="Saving…"
		success="{nameOf(path)} is now public"
	/>
	<form
		method="POST"
		action="?/visibility"
		bind:this={unpublish}
		use:enhance={unpublishing.enhance}
		hidden
	>
		<input type="hidden" name="public" value="false" />
	</form>
{/if}
