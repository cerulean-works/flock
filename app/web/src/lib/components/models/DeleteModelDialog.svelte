<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { TRASH_DAYS, modelApi, modelCall, modelsHref } from '#lib/models.ts';
	import type { ModelInfo } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		info: ModelInfo;
	}

	let { open = $bindable(), org, info }: Props = $props();

	// Typing the name is the confirmation; reset whenever the dialog opens.
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
			await modelCall(modelApi(org, info.name), { method: 'DELETE' });
			toast.success(`Deleted ${info.model}`);
			open = false;
			await goto(modelsHref(org), { replace: true });
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
			<div class="grid gap-2">
				<Label for="delete-model-name">
					Type <span class="font-mono text-[12.5px]">{info.model}</span> to confirm
				</Label>
				<Input
					id="delete-model-name"
					bind:value={typed}
					autocomplete="off"
					spellcheck="false"
					disabled={pending}
				/>
			</div>
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
