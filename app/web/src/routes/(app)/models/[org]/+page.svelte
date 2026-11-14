<script lang="ts">
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import SelectionBar from '#lib/components/SelectionBar.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import HFImportButton from '#lib/components/imports/HFImportButton.svelte';
	import HFImportRows from '#lib/components/imports/HFImportRows.svelte';
	import ModelList from '#lib/components/models/ModelList.svelte';
	import Page from '#lib/components/Page.svelte';
	import { HFImportList } from '#lib/hf.svelte.ts';
	import { Selection } from '#lib/selection.svelte.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	// Members and admins import from Hugging Face (D124); the imports show above the list.
	const writer = $derived(data.shell.access === 'admin' || data.shell.access === 'member');
	const imports = $derived(new HFImportList(params.org));
	$effect(() => (writer ? imports.watch() : undefined));
	// Models checked here (D130), cleared when the org changes.
	const selection = $derived.by(() => {
		void params.org;
		return writer ? new Selection() : undefined;
	});
</script>

<svelte:head>
	<title>Models · {data.shell.orgName}</title>
</svelte:head>

<Page width="home">
	<div class="flex items-center gap-3 pt-8 pb-5">
		<h1 class="min-w-0 flex-1 text-[34px] leading-[1.15] tracking-[-0.025em]">Models</h1>
		{#if selection?.size}
			<SelectionBar {selection}>
				<Button
					variant="outline"
					size="sm"
					class="text-destructive"
					onclick={() => selection.act('delete')}
				>
					<Trash2Icon /> Delete
				</Button>
			</SelectionBar>
		{:else if writer}
			<HFImportButton org={params.org} kind="model" list={imports} />
		{/if}
	</div>
	<HFImportRows org={params.org} list={imports} kind="model" />
	<ModelList org={params.org} models={data.models} now={data.now} {selection} />
</Page>
