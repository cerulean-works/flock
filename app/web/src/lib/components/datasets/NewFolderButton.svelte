<script lang="ts">
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import { DATASETS_BUCKET } from '#lib/datasets.ts';
	import CreateFolderDialog from '../storage/CreateFolderDialog.svelte';

	// A folder of flock-datasets, made as the Storage tab makes one. The page reads the listing
	// again once it's there.
	interface Props {
		org: string;
		/** The folder shown: empty for the bucket's root. */
		path: string;
	}

	let { org, path }: Props = $props();

	let open = $state(false);
</script>

<Button variant="outline" size="sm" onclick={() => (open = true)}>
	<FolderPlusIcon /> New folder
</Button>
<CreateFolderDialog
	bind:open
	{org}
	bucket={DATASETS_BUCKET}
	prefix={path ? `${path}/` : ''}
	oncreated={() => invalidateAll()}
/>
