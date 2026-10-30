<script lang="ts">
	import Breadcrumbs from '#lib/components/storage/Breadcrumbs.svelte';
	import ModelView from '#lib/components/models/ModelView.svelte';
	import { modelsHref } from '#lib/models.ts';
	import Page from '#lib/components/Page.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	const trail = $derived([
		{ label: 'Models', href: modelsHref(params.org) },
		{ label: data.shell.orgName ?? params.org, href: modelsHref(params.org) },
		{ label: params.name, href: modelsHref(params.org, params.name) }
	]);
</script>

<svelte:head>
	<title>{params.name} · Models · {data.shell.orgName}</title>
</svelte:head>

<Page width="home">
	<div class="pt-5"><Breadcrumbs crumbs={trail} /></div>
	{#key params.name}
		<ModelView
			org={params.org}
			info={data.info}
			version={data.version}
			requested={data.requested}
			now={data.now}
		/>
	{/key}
</Page>
