<script lang="ts">
	import { enhance } from '$app/forms';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import FormError from '#lib/components/FormError.svelte';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { DatasetInfo } from '#lib/types.ts';

	interface Props {
		open: boolean;
		dataset: DatasetInfo;
	}

	let { open = $bindable(), dataset }: Props = $props();

	const mutation = new Mutation({
		success: () => `Deleted ${dataset.name}`,
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
			<AlertDialog.Title>Delete {dataset.name}?</AlertDialog.Title>
			<AlertDialog.Description>
				{#if dataset.latest_version != null}
					Deletes every version and the files they use. Scripts loading {dataset.org}/{dataset.name}
					stop working. There is no undo.
				{:else}
					It has no versions yet. There is no undo.
				{/if}
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form method="POST" action="?/delete" class="grid gap-4" use:enhance={mutation.enhance}>
			<div class="grid gap-2">
				<Label for="delete-dataset-name">
					Type <span class="font-mono text-[12.5px]">{dataset.name}</span> to confirm
				</Label>
				<Input id="delete-dataset-name" bind:value={typed} autocomplete="off" spellcheck="false" />
			</div>
			<FormError error={mutation.error} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				<Button
					type="submit"
					variant="destructive"
					disabled={typed !== dataset.name || mutation.pending}
				>
					{mutation.pending ? 'Deleting…' : 'Delete dataset'}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
