<script lang="ts">
	import ModelList from '#lib/components/models/ModelList.svelte';
	import Page from '#lib/components/Page.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();
</script>

<svelte:head>
	<title>Models · {data.shell.orgName}</title>
</svelte:head>

<Page width="home">
	<h1 class="pt-8 pb-5 text-[34px] leading-[1.15] tracking-[-0.025em]">Models</h1>
	<ModelList org={params.org} models={data.models} now={data.now} />
</Page>
