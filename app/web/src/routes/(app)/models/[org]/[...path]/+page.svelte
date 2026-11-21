<script lang="ts">
	import ModelsPage from '#lib/components/models/ModelsPage.svelte';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();
</script>

<ModelsPage scope={{ org: params.org, project: null }} {data} shell={data.shell} />
