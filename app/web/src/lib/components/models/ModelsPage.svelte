<script lang="ts">
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import ListPage from '#lib/components/ListPage.svelte';
	import { headerTrail } from '#lib/storage.ts';
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
	const trail = $derived(
		scopeCrumbs(scope, shell.orgName ?? scope.org, 'Models', data.path, modelsHref)
	);
	// The subtitle's trail, under the title "Models": an org's leaves out its own "Models".
	// Models › folders: without the org's crumb, or the project's, which the header leads with.
	const subtrail = $derived(trail.filter((_, i) => i !== (scope.project === null ? 1 : 0)));
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
	// The header leads with the org (and project) by itself: Models › folders, the last the title.
	const header = $derived(headerTrail(subtrail, 'Models'));
</script>

<svelte:head>
	<title
		>{data.path ? `${nameOf(data.path)} · Models` : 'Models'}{project === null
			? ''
			: ` · ${project}`} · {shell.orgName}</title
	>
</svelte:head>

<!-- Quiet actions, as on every header. -->
{#snippet actions()}
	<NewFolderButton {scope} path={data.path} />
	<HFImportButton {scope} kind="model" list={imports} />
{/snippet}

<!-- While models are checked (D130): what to do with them. -->
{#snippet selected()}
	{#if selection}
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
		title={header.title}
		trail={header.trail}
		org={false}
		actions={writer ? actions : undefined}
		{selection}
		{selected}
	>
		<!-- Imports writing into this folder, over its listing. -->
		<div class="px-5 has-[li]:pt-4">
			<HFImportRows {scope} list={imports} kind="model" folder={data.path} />
		</div>
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
	</ListPage>
{:else}
	{#key data.path}
		<ModelView
			{scope}
			trail={header.trail}
			info={data.info}
			version={data.version}
			requested={data.requested}
			now={data.now}
		/>
	{/key}
{/if}
