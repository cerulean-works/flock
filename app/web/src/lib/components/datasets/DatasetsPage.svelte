<script lang="ts">
	import BlendIcon from '@lucide/svelte/icons/blend';
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import Page from '#lib/components/Page.svelte';
	import ListPage from '#lib/components/ListPage.svelte';
	import Breadcrumbs from '#lib/components/Breadcrumbs.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import HFImportButton from '#lib/components/imports/HFImportButton.svelte';
	import HFImportRows from '#lib/components/imports/HFImportRows.svelte';
	import { crumbs, nameOf } from '#lib/datasets.ts';
	import { DatasetJobList } from '#lib/forks.svelte.ts';
	import type { BlendStart } from '#lib/forks.ts';
	import { HFImportList } from '#lib/hf.svelte.ts';
	import type { Scope } from '#lib/scope.ts';
	import { Selection } from '#lib/selection.svelte.ts';
	import type { Access, DatasetsPath, ProjectInfo } from '#lib/types.ts';
	import BlendDialog from './BlendDialog.svelte';
	import DatasetJobRows from './DatasetJobRows.svelte';
	import DatasetView from './DatasetView.svelte';
	import FolderView from './FolderView.svelte';
	import NewFolderButton from './NewFolderButton.svelte';

	/**
	 * A folder or a dataset of a scope (D169): the org's Datasets tab, or a project's. The same
	 * page either way; the scope picks the API's `project` and the URLs it links to.
	 */
	interface Props {
		scope: Scope;
		data: DatasetsPath;
		shell: { access: Access; orgName: string | null; projects: ProjectInfo[] };
	}

	let { scope, data, shell }: Props = $props();

	const project = $derived(scope.project);
	const title = $derived(data.path ? nameOf(data.path) : 'Datasets');
	// Outside the org its folders can't be opened (D114): no trail to them.
	const trail = $derived(
		shell.access === 'public' ? [] : crumbs(scope, shell.orgName ?? scope.org, data.path)
	);
	// Members and admins import from Hugging Face (D124); the imports show in their folder.
	const writer = $derived(shell.access === 'admin' || shell.access === 'member');
	const imports = $derived(new HFImportList(scope.org));
	$effect(() => (writer && data.kind === 'folder' ? imports.watch() : undefined));
	// Blends running into this folder (D176), the org's jobs filtered by where they write.
	const jobs = $derived(new DatasetJobList(scope.org));
	$effect(() => (writer && data.kind === 'folder' ? jobs.watch() : undefined));
	let blending = $state(false);
	let blendStart = $state<BlendStart | null>(null);
	/** The blend dialog, with the datasets checked as its sources (none for "New blend"). */
	function blend(paths: readonly string[] = []) {
		const datasets = new Set(data.kind === 'folder' ? data.folder.datasets.map((d) => d.path) : []);
		const chosen = paths.filter((p) => datasets.has(p));
		blendStart = { sources: chosen.map((path) => ({ path })) };
		blending = true;
	}
	// Datasets and folders checked here (D130), cleared when the folder or scope changes.
	const selection = $derived.by(() => {
		void [scope.org, scope.project, data.path];
		return writer ? new Selection() : undefined;
	});
	// Org scope: Datasets › org › folders; a project's: project › Datasets › folders.
	const subtrail = $derived(trail.filter((_, i) => i !== (project === null ? 0 : 1)));
	const projects = $derived(shell.projects.map((p) => p.name));
</script>

<svelte:head>
	<title
		>{data.path ? `${title} · Datasets` : 'Datasets'}{project === null ? '' : ` · ${project}`} · {shell.orgName}</title
	>
</svelte:head>

{#snippet trailCrumbs()}<Breadcrumbs crumbs={subtrail} />{/snippet}

{#snippet actions()}
	<!-- One filled action (the import), with the quieter ones beside it. -->
	{#if writer}
		<Button variant="ghost" onclick={() => blend()}>
			<BlendIcon /> <span class="max-sm:sr-only">New blend</span>
		</Button>
	{/if}
	<NewFolderButton {scope} path={data.path} />
	{#if writer}
		<HFImportButton {scope} kind="dataset" list={imports} folder={data.path} />
	{/if}
{/snippet}

<!-- While rows are checked: what to do with them. -->
{#snippet selected()}
	{#if selection}
		<Button variant="outline" size="sm" onclick={() => blend(selection.chosen)}>
			<BlendIcon /> Blend
		</Button>
		<Button variant="outline" size="sm" onclick={() => selection.act('move')}>
			<FolderInputIcon /> Move
		</Button>
		<Button
			variant="outline"
			size="sm"
			class="text-destructive"
			onclick={() => selection.act('delete')}
		>
			<Trash2Icon /> Delete
		</Button>
	{/if}
{/snippet}

{#if data.kind === 'folder'}
	<ListPage
		title="Datasets"
		heading="All datasets"
		location={subtrail.length ? trailCrumbs : undefined}
		{actions}
		{selection}
		{selected}
	>
		<!-- Imports and blends writing into this folder, over its listing. -->
		<div class="px-5 has-[li]:pt-4">
			<HFImportRows {scope} list={imports} kind="dataset" folder={data.path} />
			<DatasetJobRows {scope} list={jobs} folder={data.path} />
		</div>
		{#key data.path}
			<FolderView
				{scope}
				path={data.path}
				folder={data.folder}
				now={data.now}
				{selection}
				{projects}
			/>
		{/key}
		{#if writer}
			<BlendDialog
				bind:open={blending}
				{scope}
				folder={data.path}
				start={blendStart}
				onstarted={(job) => jobs.add(job)}
			/>
		{/if}
	</ListPage>
{:else}
	<Page width="home">
		<div class="pt-5">
			{#if trail.length}<Breadcrumbs crumbs={trail} />{/if}
		</div>
		{#key data.path}
			<DatasetView
				{scope}
				path={data.path}
				info={data.info}
				failed={data.failed}
				requested={data.requested}
				s3={data.s3}
				objectKey={data.key}
				access={shell.access}
				now={data.now}
				{projects}
			/>
		{/key}
	</Page>
{/if}
