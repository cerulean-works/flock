<script lang="ts">
	import Breadcrumbs from '#lib/components/storage/Breadcrumbs.svelte';
	import ObjectBrowser from '#lib/components/storage/ObjectBrowser.svelte';
	import ObjectView from '#lib/components/storage/ObjectView.svelte';
	import { crumbs as crumbsFor, nameOf } from '#lib/storage.ts';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	const title = $derived(data.key ? nameOf(data.key) : params.bucket);
	const trail = $derived(
		crumbsFor(params.org, data.shell.orgName ?? params.org, params.bucket, data.key)
	);
</script>

<svelte:head>
	<title>{title} · Storage · {data.shell.orgName}</title>
</svelte:head>

<Page width="home">
	<PageHeader title={data.kind === 'folder' && data.key ? `${title}/` : title} layout="home">
		{#snippet crumbs()}
			<Breadcrumbs crumbs={trail} />
		{/snippet}
	</PageHeader>
	{#if data.kind === 'folder'}
		<ObjectBrowser
			org={params.org}
			bucket={params.bucket}
			prefix={data.key}
			listing={data.listing}
			now={data.now}
			projects={data.shell.projects}
		/>
	{:else}
		<ObjectView org={params.org} bucket={params.bucket} object={data.object} />
	{/if}
</Page>
