<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { modelApi, modelCall, modelNameError, modelsHref } from '#lib/models.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		/** The model's name now. */
		name: string;
		/** Once renamed, in place of opening the model's new page (the list's row menu). */
		onrenamed?: (to: string) => void | Promise<void>;
	}

	let { open = $bindable(), org, name, onrenamed }: Props = $props();

	// Reset once the dialog has closed, so its text stays put while it animates closed.
	let to = $derived(name);
	let error = $state<string | null>(null);
	let pending = $state(false);

	async function submit() {
		const target = to.trim();
		const invalid = target === name ? 'Enter a new name' : modelNameError(target);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			await modelCall(modelApi(org, name), { method: 'PATCH', body: { name: target } });
			toast.success(`Renamed to ${target}`);
			open = false;
			if (onrenamed) await onrenamed(target);
			else await goto(modelsHref(org, target), { replace: true });
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<Dialog.Root
	bind:open={() => open, (o) => (open = pending ? true : o)}
	onOpenChangeComplete={(o) => {
		if (o) return;
		to = name;
		error = null;
	}}
>
	<Dialog.Content
		class="sm:max-w-md"
		showCloseButton={!pending}
		escapeKeydownBehavior={pending ? 'ignore' : 'close'}
		interactOutsideBehavior={pending ? 'ignore' : 'close'}
	>
		<Dialog.Header>
			<Dialog.Title class="break-all">Rename {name}</Dialog.Title>
			<Dialog.Description>
				Code that loads it by its old name stops working. Its files stay where they are.
			</Dialog.Description>
		</Dialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				void submit();
			}}
		>
			<div class="grid gap-2">
				<Label for="rename-model-name">New name</Label>
				<Input
					id="rename-model-name"
					class="font-mono text-[13px]"
					bind:value={to}
					disabled={pending}
					autocomplete="off"
					spellcheck="false"
				/>
			</div>
			<FormError {error} />
			<Dialog.Footer>
				<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}>
					Cancel
				</Button>
				<Button type="submit" disabled={pending}>
					{#if pending}<LoaderCircleIcon class="animate-spin" /> Renaming…{:else}Rename{/if}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
