<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { invalidateAll } from '$app/navigation';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { TrashEntry } from '#lib/types.ts';
	import FormError from '../FormError.svelte';
	import ConfirmField from '../ConfirmField.svelte';

	let {
		open = $bindable(),
		org,
		entry
	}: { open: boolean; org: string; entry: TrashEntry | null } = $props();

	// Typing the path is the confirmation. Reset once the dialog has closed, so its text stays
	// put while it animates closed.
	let typed = $state('');
	let error = $state<string | null>(null);
	let pending = $state(false);

	async function purge() {
		if (!entry) return;
		pending = true;
		error = null;
		try {
			await datasetCall(datasetsApi(org, `trash/${entry.id}`), { method: 'DELETE' });
			toast.success(`Deleted ${entry.path} for good`);
			open = false;
			await invalidateAll();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AlertDialog.Root
	bind:open
	onOpenChangeComplete={(o) => {
		if (o) return;
		typed = '';
		error = null;
	}}
>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {entry?.path} for good?</AlertDialog.Title>
			<AlertDialog.Description>
				It’s removed from the trash with {entry?.kind === 'folder'
					? 'everything in it'
					: 'all its versions'}. There is no undo.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (typed === entry?.path) void purge();
			}}
		>
			<ConfirmField id="purge-dataset-path" text={entry?.path ?? ''} bind:value={typed} />
			<FormError {error} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				<Button
					type="submit"
					variant="destructive"
					disabled={!entry || typed !== entry.path || pending}
				>
					{pending ? 'Deleting…' : 'Delete for good'}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
