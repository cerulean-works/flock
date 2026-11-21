<script lang="ts">
	import Breadcrumbs from '#lib/components/Breadcrumbs.svelte';
	import ObjectBrowser from '#lib/components/storage/ObjectBrowser.svelte';
	import ObjectView from '#lib/components/storage/ObjectView.svelte';
	import { crumbs as crumbsFor, nameOf } from '#lib/storage.ts';
	import ListPage from '#lib/components/ListPage.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	// Storage › bucket › folders…: the org's crumb is the same page as Storage's, so it's left out.
	const trail = $derived(
		crumbsFor(params.org, data.shell.orgName ?? params.org, params.bucket, data.key).filter(
			(_, i) => i !== 1
		)
	);
</script>

<svelte:head>
	<title>{data.key ? nameOf(data.key) : params.bucket} · Storage · {data.shell.orgName}</title>
</svelte:head>

{#snippet path()}<Breadcrumbs crumbs={trail} />{/snippet}

{#if data.kind === 'folder'}
	<ObjectBrowser
		org={params.org}
		bucket={params.bucket}
		prefix={data.key}
		listing={data.listing}
		now={data.now}
		projects={data.shell.projects}
		title={params.bucket}
		crumbs={trail}
	/>
{:else}
	<ListPage title={params.bucket} location={path} class="px-5 pt-4">
		<ObjectView org={params.org} bucket={params.bucket} object={data.object} />
	</ListPage>
{/if}
