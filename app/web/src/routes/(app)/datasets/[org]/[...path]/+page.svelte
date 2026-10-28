<script lang="ts">
	import Breadcrumbs from '#lib/components/storage/Breadcrumbs.svelte';
	import DatasetView from '#lib/components/datasets/DatasetView.svelte';
	import FolderView from '#lib/components/datasets/FolderView.svelte';
	import { crumbs, nameOf } from '#lib/datasets.ts';
	import Page from '#lib/components/Page.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	const title = $derived(data.path ? nameOf(data.path) : 'Datasets');
	const trail = $derived(crumbs(params.org, data.shell.orgName ?? params.org, data.path));
</script>

<svelte:head>
	<title>{data.path ? `${title} · Datasets` : 'Datasets'} · {data.shell.orgName}</title>
</svelte:head>

<Page width="home">
	{#if trail.length}
		<div class="pt-5"><Breadcrumbs crumbs={trail} /></div>
	{/if}
	{#if data.kind === 'folder'}
		<h1
			class={[
				'truncate pb-5 text-[34px] leading-[1.15] tracking-[-0.025em]',
				trail.length ? 'pt-3' : 'pt-8'
			]}
			{title}
		>
			{title}
		</h1>
		{#key data.path}
			<FolderView org={params.org} path={data.path} folder={data.folder} now={data.now} />
		{/key}
	{:else}
		{#key data.path}
			<DatasetView
				org={params.org}
				path={data.path}
				info={data.info}
				failed={data.failed}
				requested={data.requested}
				s3={data.s3}
				now={data.now}
			/>
		{/key}
	{/if}
</Page>
