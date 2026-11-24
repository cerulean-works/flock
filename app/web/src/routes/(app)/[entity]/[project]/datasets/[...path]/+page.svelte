<script lang="ts">
	import DatasetsPage from '#lib/components/datasets/DatasetsPage.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();
</script>

<DatasetsPage scope={{ org: params.entity, project: params.project }} {data} shell={data.shell} />
