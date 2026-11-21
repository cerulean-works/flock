<script lang="ts">
	import ModelsPage from '#lib/components/models/ModelsPage.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();
</script>

<ModelsPage scope={{ org: params.entity, project: params.project }} {data} shell={data.shell} />
