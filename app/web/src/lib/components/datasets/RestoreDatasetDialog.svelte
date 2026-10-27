<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { DatasetError, datasetCall, datasetsApi, pathError } from '#lib/datasets.ts';
	import type { TrashEntry } from '#lib/types.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		entry: TrashEntry | null;
	}

	let { open = $bindable(), org, entry }: Props = $props();

	/** Something now exists at the old path: the dialog asks for another one. */
	let taken = $derived.by(() => {
		void open;
		return false;
	});
	let to = $derived.by(() => {
		void open;
		return entry ? `${entry.path}-restored` : '';
	});
	let error = $derived.by((): string | null => {
		void open;
		return null;
	});
	let pending = $state(false);

	async function restore() {
		if (!entry) return;
		const target = taken ? to.trim() : null;
		if (target !== null) {
			const invalid = pathError(target);
			if (invalid) {
				error = invalid;
				return;
			}
		}
		error = null;
		pending = true;
		try {
			const done = await datasetCall<{ path: string }>(
				datasetsApi(org, `trash/${entry.id}/restore`),
				{ method: 'POST', body: target === null ? {} : { to: target } }
			);
			toast.success(`Restored ${done.path}`);
			open = false;
			await invalidateAll();
		} catch (e) {
			if (e instanceof DatasetError && e.status === 409 && e.body.taken && !taken) taken = true;
			else error = (e as Error).message;
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
			<Dialog.Title>Restore {entry?.path}?</Dialog.Title>
			<Dialog.Description>
				{#if taken}
					Something already exists at {entry?.path}. Restore it to another path.
				{:else}
					It goes back to {entry?.path} with all its versions.
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
			{#if taken}
				<div class="grid gap-2">
					<Label for="restore-path">Path</Label>
					<Input
						id="restore-path"
						class="font-mono text-[13px]"
						bind:value={to}
						disabled={pending}
						autocomplete="off"
						spellcheck="false"
					/>
				</div>
			{/if}
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
