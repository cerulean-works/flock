<script lang="ts">
	import { enhance } from '$app/forms';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import FormError from '../FormError.svelte';
	import ConfirmField from '../ConfirmField.svelte';

	let { open = $bindable(), bucket }: { open: boolean; bucket: string } = $props();

	const mutation = new Mutation({
		success: () => `Deleted ${bucket}`,
		done: () => (open = false)
	});

	// Typing the name is the confirmation; reset whenever the dialog opens.
	let typed = $derived.by(() => {
		void open;
		return '';
	});
</script>

<AlertDialog.Root bind:open onOpenChange={(o) => o || mutation.reset()}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {bucket}?</AlertDialog.Title>
			<AlertDialog.Description>
				Deletes the bucket and every object in it, for everyone in the organization. There is no
				undo. The name can be used again once the S3 server has emptied it.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form method="POST" action="?/deleteBucket" class="grid gap-4" use:enhance={mutation.enhance}>
			<input type="hidden" name="bucket" value={bucket} />
			<ConfirmField id="delete-bucket-name" text={bucket} bind:value={typed} />
			<FormError error={mutation.error} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				<Button type="submit" variant="destructive" disabled={typed !== bucket || mutation.pending}>
					{mutation.pending ? 'Deleting…' : 'Delete bucket'}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
