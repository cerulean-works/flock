<script lang="ts">
	import { page } from '$app/state';
	import Page from '#lib/components/Page.svelte';
	import GroupEditor from '#lib/components/groups/GroupEditor.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const entity = $derived(page.params.entity!);
	const project = $derived(page.params.project!);
</script>

<svelte:head>
	<title>Edit {data.group.name} · {project} · {data.shell.orgName ?? entity}</title>
</svelte:head>

<Page width="full">
	{#key data.group.id}
		<GroupEditor
			{entity}
			{project}
			runs={data.runs}
			summaryKeys={data.summaryKeys}
			people={data.people}
			groupList={data.groupList}
			group={data.group}
			fresh={data.fresh}
		/>
	{/key}
</Page>
