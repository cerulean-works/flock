<script lang="ts">
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import SelectionBar from '#lib/components/SelectionBar.svelte';
	import Breadcrumbs from '#lib/components/storage/Breadcrumbs.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import HFImportButton from '#lib/components/imports/HFImportButton.svelte';
	import HFImportRows from '#lib/components/imports/HFImportRows.svelte';
	import { nameOf, scopeCrumbs } from '#lib/datasets.ts';
	import { HFImportList } from '#lib/hf.svelte.ts';
	import { modelsHref } from '#lib/models.ts';
	import type { Scope } from '#lib/scope.ts';
	import { Selection } from '#lib/selection.svelte.ts';
	import type { Access, ModelsPath, ProjectInfo } from '#lib/types.ts';
	import ModelList from './ModelList.svelte';
	import ModelView from './ModelView.svelte';
	import NewFolderButton from './NewFolderButton.svelte';

	/**
	 * A folder or a model of a scope (D158, D169): the org's Models tab, or a project's. The same
	 * page either way; the scope picks the API's `project` and the URLs it links to.
	 */
	interface Props {
		scope: Scope;
		data: ModelsPath;
		shell: { access: Access; orgName: string | null; projects: ProjectInfo[] };
	}

	let { scope, data, shell }: Props = $props();

	const project = $derived(scope.project);
	const title = $derived(data.path ? nameOf(data.path) : 'Models');
	const trail = $derived(
		scopeCrumbs(scope, shell.orgName ?? scope.org, 'Models', data.path, modelsHref)
	);
	// Members and admins import from Hugging Face (D124); the imports show in their folder.
	const writer = $derived(shell.access === 'admin' || shell.access === 'member');
	const imports = $derived(new HFImportList(scope.org));
	$effect(() => (writer && data.kind === 'folder' ? imports.watch() : undefined));
	// Models checked here (D130), cleared when the folder or scope changes.
	const selection = $derived.by(() => {
		void [scope.org, scope.project, data.path];
		return writer ? new Selection() : undefined;
	});
	const projects = $derived(shell.projects.map((p) => p.name));
</script>

<svelte:head>
	<title
		>{data.path ? `${title} · Models` : 'Models'}{project === null ? '' : ` · ${project}`} · {shell.orgName}</title
	>
</svelte:head>

{#snippet trailCrumbs()}<Breadcrumbs crumbs={trail} />{/snippet}

{#snippet actions()}
	{#if selection?.size}
		<SelectionBar {selection}>
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
		</SelectionBar>
	{:else}
		<!-- One filled action (the import), with the quieter one beside it. -->
		<NewFolderButton {scope} path={data.path} />
		<HFImportButton {scope} kind="model" list={imports} />
	{/if}
{/snippet}

<Page width="home">
	{#if data.kind === 'folder'}
		<PageHeader
			{title}
			layout="home"
			crumbs={trail.length ? trailCrumbs : undefined}
			actions={writer ? actions : undefined}
		/>
		<HFImportRows {scope} list={imports} kind="model" folder={data.path} />
		{#key data.path}
			<ModelList
				{scope}
				path={data.path}
				folder={data.folder}
				now={data.now}
				{selection}
				{projects}
			/>
		{/key}
	{:else}
		{#if trail.length}
			<div class="pt-5"><Breadcrumbs crumbs={trail} /></div>
		{/if}
		{#key data.path}
			<ModelView
				{scope}
				info={data.info}
				version={data.version}
				requested={data.requested}
				now={data.now}
			/>
		{/key}
	{/if}
</Page>
