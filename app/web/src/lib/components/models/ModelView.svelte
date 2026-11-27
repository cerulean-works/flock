<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import FolderOpenIcon from '@lucide/svelte/icons/folder-open';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import DetailPage from '#lib/components/DetailPage.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import VersionPicker from '#lib/components/VersionPicker.svelte';
	import { nameOf } from '#lib/datasets.ts';
	import { folderHref, modelApi, modelCall, sortTags } from '#lib/models.ts';
	import type { Crumb } from '#lib/storage.ts';
	import type { ModelInfo, ModelVersionDetail, ModelVersionsPage } from '#lib/types.ts';
	import { withParam } from '#lib/workspace/url.ts';
	import DeleteModelDialog from './DeleteModelDialog.svelte';
	import EvalsTab from './EvalsTab.svelte';
	import FilesTab from './FilesTab.svelte';
	import LoadDialog from './LoadDialog.svelte';
	import ModelProperties from './ModelProperties.svelte';
	import OverviewTab from './OverviewTab.svelte';
	import RenameModelDialog from './RenameModelDialog.svelte';
	import { openMenuTrigger } from '../AnchoredForm.svelte';
	import VersionsTab from './VersionsTab.svelte';

	interface Props {
		scope: Scope;
		/** The header's steps before the name, such as Models › baselines. */
		trail?: Crumb[];
		info: ModelInfo;
		/** The version shown; null while the model has none. */
		version: ModelVersionDetail | null;
		/** The `?version=` asked for (a number or a tag), null for the primary. */
		requested: string | null;
		now: number;
	}

	let { scope, trail = [], info, version, requested, now }: Props = $props();

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

	/** The newest versions for the picker, each with the primary and its tags. */
	const readVersions = (signal: AbortSignal) =>
		modelCall<ModelVersionsPage>(modelApi(scope, info.name, 'versions'), { signal }).then((p) =>
			p.versions.map((v) => ({
				number: v.number,
				names: v.primary ? ['primary', ...sortTags(v.tags)] : sortTags(v.tags)
			}))
		);

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

	const filesHref = $derived(version ? folderHref(scope.org, version.prefix) : null);

	let loading = $state(false);
	let renaming = $state(false);
	let renameAnchor = $state<HTMLElement | null>(null);
	let deleting = $state(false);
</script>

{#snippet badges()}
	{#if version}
		<VersionPicker
			version={version.number}
			{requested}
			primary={info.primary}
			mark={version.primary ? 'Primary' : null}
			tags={sortTags(info.tags)}
			read={readVersions}
			{versionHref}
		/>
	{/if}
{/snippet}

{#snippet controls()}
	{#if version}
		<Button variant="quiet" size="bar" onclick={() => (loading = true)}>
			<DownloadIcon /> <span class="max-sm:sr-only">Load</span>
		</Button>
	{/if}
	<!-- Renaming, the files and deletion: less often, so tucked away, as a run's and a
	     project's are. -->
	<DropdownMenu.Root>
		<DropdownMenu.Trigger>
			{#snippet child({ props })}
				<Button {...props} variant="quiet" size="icon-bar" aria-label="More model actions">
					<EllipsisIcon />
				</Button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content align="end" class="w-48">
			<DropdownMenu.Item
				onSelect={() => {
					renameAnchor = openMenuTrigger();
					renaming = true;
				}}
			>
				<PencilIcon /> Rename…
			</DropdownMenu.Item>
			{#if filesHref}
				<DropdownMenu.Item>
					{#snippet child({ props })}
						<a {...props} href={filesHref}>
							<FolderOpenIcon /> Browse files
						</a>
					{/snippet}
				</DropdownMenu.Item>
				<DropdownMenu.Separator />
			{/if}
			<DropdownMenu.Item variant="destructive" onSelect={() => (deleting = true)}>
				<Trash2Icon /> Delete…
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/snippet}

{#snippet properties()}
	{#if version}<ModelProperties {info} {version} />{/if}
{/snippet}

<DetailPage
	title={nameOf(info.name)}
	{trail}
	titleHint={info.model}
	{badges}
	tabs={TABS}
	{tab}
	ontab={setTab}
	tabsLabel="Model"
	{controls}
	properties={tab === 'overview' && version ? properties : undefined}
	flush={tab === 'versions' || (tab === 'files' && !!version)}
>
	{#if tab === 'versions'}
		<VersionsTab
			{scope}
			{info}
			shown={version?.number ?? null}
			{versionHref}
			onchange={refresh}
			{now}
		/>
	{:else if !version}
		<p class="text-[13px] text-muted-foreground">
			No versions yet. A version appears once its upload finishes.
		</p>
	{:else if tab === 'overview'}
		{#key version.number}
			<OverviewTab {scope} {version} />
		{/key}
	{:else if tab === 'evals'}
		{#key `${scope.org}/${version.model}@${version.number}`}
			<EvalsTab {scope} {version} />
		{/key}
	{:else}
		<FilesTab {scope} {version} />
	{/if}
</DetailPage>

{#if version}
	<LoadDialog bind:open={loading} {scope} name={info.name} number={version.number} />
{/if}
<RenameModelDialog bind:open={renaming} anchor={renameAnchor} {scope} name={info.name} />
<DeleteModelDialog bind:open={deleting} {scope} {info} />
