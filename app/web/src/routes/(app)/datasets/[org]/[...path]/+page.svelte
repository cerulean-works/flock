<script lang="ts">
	import DatasetsPage from '#lib/components/datasets/DatasetsPage.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();
</script>

<DatasetsPage scope={{ org: params.org, project: null }} {data} shell={data.shell} />
