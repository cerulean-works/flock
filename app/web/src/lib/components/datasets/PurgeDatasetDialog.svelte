<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { invalidateAll } from '$app/navigation';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { TrashEntry } from '#lib/types.ts';
	import FormError from '../FormError.svelte';

	let {
		open = $bindable(),
		org,
		entry
	}: { open: boolean; org: string; entry: TrashEntry | null } = $props();

	// Typing the path is the confirmation; reset whenever the dialog opens.
	let typed = $derived.by(() => {
		void open;
		return '';
	});
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

<AlertDialog.Root bind:open onOpenChange={(o) => o || (error = null)}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {entry?.path} for good?</AlertDialog.Title>
			<AlertDialog.Description>
				It’s removed from the trash with all its versions. There is no undo.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (typed === entry?.path) void purge();
			}}
		>
			<div class="grid gap-2">
				<Label for="purge-dataset-path">
					Type <span class="font-mono text-[12.5px]">{entry?.path}</span> to confirm
				</Label>
				<Input id="purge-dataset-path" bind:value={typed} autocomplete="off" spellcheck="false" />
			</div>
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
