<script lang="ts">
	import DownloadIcon from '@lucide/svelte/icons/download';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { HFImportList } from '#lib/hf.svelte.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { HFImport } from '#lib/types.ts';
	import HFDatasetDialog from './HFDatasetDialog.svelte';
	import HFModelDialog from './HFModelDialog.svelte';

	// "Import from Hugging Face" on the Datasets and Models tabs (D124), for members and admins.
	interface Props {
		/** Where the import lands: the org's global scope or one of its projects (D159). */
		scope: Scope;
		kind: 'dataset' | 'model';
		list: HFImportList;
		/** The folder shown, for datasets. */
		folder?: string;
	}

	let { scope, kind, list, folder = '' }: Props = $props();

	let open = $state(false);

	function started(job: HFImport) {
		list.add(job);
		toast.success(`Importing ${job.repo}`);
	}
</script>

<Button variant="outline" size="sm" onclick={() => (open = true)}>
	<DownloadIcon /> Import from Hugging Face
</Button>
{#if kind === 'dataset'}
	<HFDatasetDialog bind:open {scope} {folder} onstarted={started} />
{:else}
	<HFModelDialog bind:open {scope} onstarted={started} />
{/if}
