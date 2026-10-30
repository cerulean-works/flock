<script lang="ts">
	import { toast } from 'svelte-sonner';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { modelApi, modelCall } from '#lib/models.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		name: string;
		tag: string;
		/** The version it's on. */
		version: number;
		onremoved: () => void;
	}

	let { open = $bindable(), org, name, tag, version, onremoved }: Props = $props();

	let pending = $state(false);
	let error = $state<string | null>(null);

	async function remove() {
		pending = true;
		error = null;
		try {
			await modelCall(modelApi(org, name, `tags/${encodeURIComponent(tag)}`), {
				method: 'DELETE'
			});
			toast.success(`Removed ${tag}`);
			open = false;
			onremoved();
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
			<AlertDialog.Title>Remove the tag {tag}?</AlertDialog.Title>
			<AlertDialog.Description>
				Code that loads {org}/{name}@{tag} will stop working. Version {version} stays, and only the tag
				is removed.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<FormError {error} />
		<AlertDialog.Footer>
			<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
			<Button variant="destructive" disabled={pending} onclick={remove}>
				{pending ? 'Removing…' : 'Remove tag'}
			</Button>
		</AlertDialog.Footer>
	</AlertDialog.Content>
</AlertDialog.Root>
