<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { TRASH_DAYS, modelApi, modelCall, modelsHref } from '#lib/models.ts';
	import type { ModelInfo } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import FormError from '../FormError.svelte';
	import ConfirmField from '../ConfirmField.svelte';

	interface Props {
		open: boolean;
		org: string;
		/** The model, as its page or the list has it. */
		info: Pick<ModelInfo, 'name' | 'model' | 'versions'>;
		/** Once deleted, in place of going to the list (the list's row menu). */
		ondeleted?: () => void | Promise<void>;
	}

	let { open = $bindable(), org, info, ondeleted }: Props = $props();

	// Typing the name is the confirmation. Reset once the dialog has closed, so its text stays
	// put while it animates closed.
	let typed = $state('');
	let error = $state<string | null>(null);
	let pending = $state(false);

	async function remove() {
		pending = true;
		error = null;
		try {
			await modelCall(modelApi(org, info.name), { method: 'DELETE' });
			toast.success(`Deleted ${info.model}`);
			open = false;
			if (ondeleted) await ondeleted();
			else await goto(modelsHref(org), { replace: true });
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<AlertDialog.Root
	bind:open={() => open, (o) => (open = pending ? true : o)}
	onOpenChangeComplete={(o) => {
		if (o) return;
		typed = '';
		error = null;
	}}
>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {info.name}?</AlertDialog.Title>
			<AlertDialog.Description>
				{#if info.versions}
					This deletes {info.versions === 1
						? 'its only version'
						: `all ${count(info.versions, 'version')}`}.
				{/if}
				Org admins can restore it for {TRASH_DAYS} days.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (typed === info.model) void remove();
			}}
		>
			<ConfirmField
				id="delete-model-name"
				text={info.model}
				bind:value={typed}
				disabled={pending}
			/>
			<FormError {error} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel disabled={pending}>Cancel</AlertDialog.Cancel>
				<Button type="submit" variant="destructive" disabled={typed !== info.model || pending}>
					{pending ? 'Deleting…' : 'Delete model'}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
