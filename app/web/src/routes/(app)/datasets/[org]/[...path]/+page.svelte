<script lang="ts">
	import Breadcrumbs from '#lib/components/storage/Breadcrumbs.svelte';
	import DatasetView from '#lib/components/datasets/DatasetView.svelte';
	import FolderView from '#lib/components/datasets/FolderView.svelte';
	import NewFolderButton from '#lib/components/datasets/NewFolderButton.svelte';
	import HFImportButton from '#lib/components/imports/HFImportButton.svelte';
	import HFImportRows from '#lib/components/imports/HFImportRows.svelte';
	import { crumbs, nameOf } from '#lib/datasets.ts';
	import { HFImportList } from '#lib/hf.svelte.ts';
	import Page from '#lib/components/Page.svelte';
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
			{#if writer}
				<HFImportButton org={params.org} kind="dataset" list={imports} folder={data.path} />
			{/if}
			<NewFolderButton org={params.org} path={data.path} />
		</div>
		<HFImportRows org={params.org} list={imports} kind="dataset" folder={data.path} />
		{#key data.path}
			<FolderView org={params.org} path={data.path} folder={data.folder} now={data.now} />
		{/key}
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
