<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { datasetCall, datasetsApi, datasetsHref, pathError } from '#lib/datasets.ts';
	import FormError from '../FormError.svelte';

	interface Props {
		open: boolean;
		org: string;
		/** The dataset's path now. */
		path: string;
	}

	let { open = $bindable(), org, path }: Props = $props();

	// Starts as the current path whenever the dialog opens: a rename is a move too.
	let to = $derived.by(() => {
		void open;
		return path;
	});
	let error = $derived.by((): string | null => {
		void open;
		return null;
	});
	let pending = $state(false);

	async function submit() {
		const target = to.trim();
		const invalid = target === path ? 'Enter a new path' : pathError(target);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			await datasetCall(datasetsApi(org, 'rename'), {
				method: 'POST',
				body: { path, to: target }
			});
			toast.success(`Renamed to ${target}`);
			open = false;
			await goto(datasetsHref(org, target), { replace: true });
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<!-- While the copy runs, the dialog can't be closed: the request decides how it ends. -->
<Dialog.Root bind:open={() => open, (o) => (open = pending ? true : o)}>
	<Dialog.Content
		class="sm:max-w-lg"
		showCloseButton={!pending}
		escapeKeydownBehavior={pending ? 'ignore' : 'close'}
		interactOutsideBehavior={pending ? 'ignore' : 'close'}
	>
		<Dialog.Header>
			<Dialog.Title>Rename {path}</Dialog.Title>
			<Dialog.Description>
				Anything still writing to the old path will create a new dataset there.
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
				<Label for="rename-path">New path</Label>
				<Input
					id="rename-path"
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
