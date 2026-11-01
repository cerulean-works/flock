<script lang="ts">
	import FileIcon from '@lucide/svelte/icons/file';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import { toast } from 'svelte-sonner';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { bucketCall, nameOf } from '#lib/storage.ts';
	import { count } from '#lib/utils.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		bucket: string;
		/** Objects to delete. */
		keys: string[];
		/** Folders to delete with everything under them. */
		prefixes: string[];
		/** Once they're gone. */
		ondeleted: () => void;
	}

	let { open = $bindable(), org, bucket, keys, prefixes, ondeleted }: Props = $props();

	let pending = $state(false);
	let error = $state<string | null>(null);

	const title = $derived(
		keys.length + prefixes.length === 1
			? `Delete ${nameOf(keys[0] ?? prefixes[0])}${prefixes.length ? '/' : ''}?`
			: `Delete ${[
					keys.length && count(keys.length, 'object'),
					prefixes.length && count(prefixes.length, 'folder')
				]
					.filter(Boolean)
					.join(' and ')}?`
	);

	async function remove() {
		pending = true;
		error = null;
		try {
			const { deleted } = await bucketCall<{ deleted: number }>(org, bucket, 'delete', {
				keys,
				prefixes
			});
			toast.success(`Deleted ${count(deleted, 'object')}`);
			open = false;
			ondeleted();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AlertDialog.Root bind:open onOpenChangeComplete={(o) => o || (error = null)}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title class="break-all">{title}</AlertDialog.Title>
			<AlertDialog.Description>
				{prefixes.length
					? 'Folders are deleted with everything in them, up to 10,000 objects in all.'
					: 'Deleted objects are gone for everyone in the organization.'}
				There is no undo.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<ul class="max-h-48 overflow-y-auto border-y border-divider py-1 text-[13px]">
			{#each prefixes as prefix (prefix)}
				<li class="flex items-center gap-2 py-0.5">
					<FolderIcon class="size-4 flex-none text-muted-foreground" />
					<span class="truncate font-mono text-[12.5px]">{prefix}</span>
				</li>
			{/each}
			{#each keys as key (key)}
				<li class="flex items-center gap-2 py-0.5">
					<FileIcon class="size-4 flex-none text-muted-foreground" />
					<span class="truncate font-mono text-[12.5px]">{key}</span>
				</li>
			{/each}
		</ul>
		<FormError {error} />
		<AlertDialog.Footer>
			<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
			<Button variant="destructive" disabled={pending} onclick={remove}>
				{pending ? 'Deleting…' : 'Delete'}
			</Button>
		</AlertDialog.Footer>
	</AlertDialog.Content>
</AlertDialog.Root>
