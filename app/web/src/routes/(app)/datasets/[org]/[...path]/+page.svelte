<script lang="ts">
	import BlendIcon from '@lucide/svelte/icons/blend';
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import SelectionBar from '#lib/components/SelectionBar.svelte';
	import Breadcrumbs from '#lib/components/storage/Breadcrumbs.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import DatasetView from '#lib/components/datasets/DatasetView.svelte';
	import BlendDialog from '#lib/components/datasets/BlendDialog.svelte';
	import DatasetJobRows from '#lib/components/datasets/DatasetJobRows.svelte';
	import FolderView from '#lib/components/datasets/FolderView.svelte';
	import NewFolderButton from '#lib/components/datasets/NewFolderButton.svelte';
	import HFImportButton from '#lib/components/imports/HFImportButton.svelte';
	import HFImportRows from '#lib/components/imports/HFImportRows.svelte';
	import { crumbs, nameOf } from '#lib/datasets.ts';
	import { DatasetJobList } from '#lib/forks.svelte.ts';
	import type { BlendStart } from '#lib/forks.ts';
	import { HFImportList } from '#lib/hf.svelte.ts';
	import Page from '#lib/components/Page.svelte';
	import { Selection } from '#lib/selection.svelte.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	const title = $derived(data.path ? nameOf(data.path) : 'Datasets');
	// Outside the org its folders can't be opened (D114): no trail to them.
	const trail = $derived(
		data.shell.access === 'public'
			? []
			: crumbs(params.org, data.shell.orgName ?? params.org, data.path)
	);
	// Members and admins import from Hugging Face (D124); the imports show in their folder.
	const writer = $derived(data.shell.access === 'admin' || data.shell.access === 'member');
	const imports = $derived(new HFImportList(params.org));
	$effect(() => (writer && data.kind === 'folder' ? imports.watch() : undefined));
	// Blends running into this folder (D176).
	const jobs = $derived(new DatasetJobList(params.org));
	$effect(() => (writer && data.kind === 'folder' ? jobs.watch() : undefined));
	let blending = $state(false);
	let blendStart = $state<BlendStart | null>(null);
	/** The blend dialog, with the datasets checked as its sources (none for "New blend"). */
	function blend(paths: readonly string[] = []) {
		const datasets = new Set(data.kind === 'folder' ? data.folder.datasets.map((d) => d.path) : []);
		blendStart = { sources: paths.filter((p) => datasets.has(p)).map((path) => ({ path })) };
		blending = true;
	}
	// Datasets and folders checked here (D130), cleared when the folder or org changes.
	const selection = $derived.by(() => {
		void params.org;
		void data.path;
		return writer ? new Selection() : undefined;
	});
</script>

<svelte:head>
	<title>{data.path ? `${title} · Datasets` : 'Datasets'} · {data.shell.orgName}</title>
</svelte:head>

<Page width="home">
	{#if trail.length}
		<div class="pt-5"><Breadcrumbs crumbs={trail} /></div>
	{/if}
	{#if data.kind === 'folder'}
		<div class={['flex items-center gap-3 pb-5', trail.length ? 'pt-3' : 'pt-8']}>
			<h1 class="min-w-0 flex-1 truncate text-[34px] leading-[1.15] tracking-[-0.025em]" {title}>
				{title}
			</h1>
			{#if selection?.size}
				<SelectionBar {selection}>
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
				</SelectionBar>
			{:else}
				{#if writer}
					<Button variant="outline" size="sm" onclick={() => blend()}>
						<BlendIcon /> New blend
					</Button>
					<HFImportButton org={params.org} kind="dataset" list={imports} folder={data.path} />
				{/if}
				<NewFolderButton org={params.org} path={data.path} />
			{/if}
		</div>
		<HFImportRows org={params.org} list={imports} kind="dataset" folder={data.path} />
		<DatasetJobRows org={params.org} list={jobs} folder={data.path} />
		{#key data.path}
			<FolderView
				org={params.org}
				path={data.path}
				folder={data.folder}
				now={data.now}
				{selection}
			/>
		{/key}
		{#if writer}
			<BlendDialog
				bind:open={blending}
				org={params.org}
				folder={data.path}
				start={blendStart}
				onstarted={(job) => jobs.add(job)}
			/>
		{/if}
	{:else}
		{#if !trail.length}<div class="pt-5"></div>{/if}
		{#key data.path}
			<DatasetView
				org={params.org}
				path={data.path}
				info={data.info}
				failed={data.failed}
				requested={data.requested}
				s3={data.s3}
				access={data.shell.access}
				now={data.now}
			/>
		{/key}
	{/if}
</Page>
