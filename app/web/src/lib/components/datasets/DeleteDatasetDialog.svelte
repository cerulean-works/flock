<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { datasetCall, datasetsApi, datasetsHref, nameOf, parentOf } from '#lib/datasets.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		path: string;
	}

	let { open = $bindable(), org, path }: Props = $props();

	// Typing the path is the confirmation; reset whenever the dialog opens.
	let typed = $derived.by(() => {
		void open;
		return '';
	});
	let error = $state<string | null>(null);
	let pending = $state(false);

	async function remove() {
		pending = true;
		error = null;
		try {
			await datasetCall(datasetsApi(org, 'delete'), { method: 'POST', body: { path } });
			toast.success(`Deleted ${nameOf(path)}`);
			open = false;
			// S3 has no folders: one that only held this dataset is gone with it, so go up to the
			// nearest that's left (the root always is).
			let to = parentOf(path);
			while (to) {
				const res = await fetch(datasetsApi(org, 'at', { path: to }));
				if (res.ok) break;
				to = parentOf(to);
			}
			await goto(datasetsHref(org, to), { replace: true });
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AlertDialog.Root bind:open={() => open, (o) => (open = pending ? true : o)}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {nameOf(path)}?</AlertDialog.Title>
			<AlertDialog.Description>Org admins can restore it for 14 days.</AlertDialog.Description>
		</AlertDialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (typed === path) void remove();
			}}
		>
			<div class="grid gap-2">
				<Label for="delete-dataset-path">
					Type <span class="font-mono text-[12.5px]">{path}</span> to confirm
				</Label>
				<Input
					id="delete-dataset-path"
					bind:value={typed}
					autocomplete="off"
					spellcheck="false"
					disabled={pending}
				/>
			</div>
			<FormError {error} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel disabled={pending}>Cancel</AlertDialog.Cancel>
				<Button type="submit" variant="destructive" disabled={typed !== path || pending}>
					{pending ? 'Deleting…' : 'Delete dataset'}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
