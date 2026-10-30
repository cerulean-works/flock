<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { modelCall, modelsApi } from '#lib/models.ts';
	import type { ModelTrashEntry } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		entry: ModelTrashEntry | null;
	}

	let { open = $bindable(), org, entry }: Props = $props();

	let error = $derived.by((): string | null => {
		void open;
		return null;
	});
	let pending = $state(false);

	const label = $derived(
		!entry ? '' : entry.kind === 'model' ? entry.model : `${entry.model}@${entry.number}`
	);

	async function restore() {
		if (!entry) return;
		error = null;
		pending = true;
		try {
			const kind = entry.kind === 'model' ? 'models' : 'versions';
			await modelCall(modelsApi(org, `-/trash/${kind}/${entry.id}/restore`), {
				method: 'POST',
				body: {}
			});
			toast.success(`Restored ${label}`);
			open = false;
			await invalidateAll();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<Dialog.Root bind:open={() => open, (o) => (open = pending ? true : o)}>
	<Dialog.Content
		class="sm:max-w-lg"
		showCloseButton={!pending}
		escapeKeydownBehavior={pending ? 'ignore' : 'close'}
		interactOutsideBehavior={pending ? 'ignore' : 'close'}
	>
		<Dialog.Header>
			<Dialog.Title>Restore {label}?</Dialog.Title>
			<Dialog.Description>
				{#if entry?.kind === 'model'}
					It comes back with the {count(entry.versions, 'version')} deleted with it, and its tags.
				{:else if entry}
					It comes back as version {entry.number}{entry.tags.length
						? `, with ${entry.tags.join(', ')}`
						: ''}.
				{/if}
			</Dialog.Description>
		</Dialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				void restore();
			}}
		>
			<FormError {error} />
			<Dialog.Footer>
				<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}>
					Cancel
				</Button>
				<Button type="submit" disabled={pending}>
					{#if pending}<LoaderCircleIcon class="animate-spin" /> Restoring…{:else}Restore{/if}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
