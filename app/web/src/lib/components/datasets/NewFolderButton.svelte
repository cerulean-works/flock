<script lang="ts">
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import { toast } from 'svelte-sonner';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import { DATASETS_BUCKET, datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { DatasetLocation } from '#lib/types.ts';
	import CreateFolderDialog from '../storage/CreateFolderDialog.svelte';

	// A folder of flock-datasets, made as the Storage tab makes one. The page reads the listing
	// again once it's there.
	interface Props {
		scope: Scope;
		/** The folder shown: empty for the bucket's root. */
		path: string;
	}

	let { scope, path }: Props = $props();

	let open = $state(false);
	/** Where the folder shown is in the bucket: a project's are under `-projects/` (D160). */
	let prefix = $state('');
	let pending = $state(false);

	async function show() {
		if (scope.project === null) {
			prefix = path ? `${path}/` : '';
			open = true;
			return;
		}
		// Only the API knows a project's folder: asked where a path in the folder would be.
		pending = true;
		try {
			const probe = `${path ? `${path}/` : ''}new-folder`;
			const where = await datasetCall<DatasetLocation>(
				datasetsApi(scope, 'location', { path: probe })
			);
			prefix = where.key.slice(0, where.key.length - 'new-folder'.length);
			open = true;
		} catch (e) {
			toast.error((e as Error).message);
		} finally {
			pending = false;
		}
	}
</script>

<Button variant="ghost" disabled={pending} onclick={show}>
	<FolderPlusIcon /> New folder
</Button>
<CreateFolderDialog
	bind:open
	org={scope.org}
	bucket={DATASETS_BUCKET}
	{prefix}
	oncreated={() => invalidateAll()}
/>
