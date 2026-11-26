<script lang="ts">
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { modelCall, modelNameError, modelsApi } from '#lib/models.ts';
	import type { Scope } from '#lib/scope.ts';
	import FormError from '../FormError.svelte';

	// An empty folder of a scope's models (D158), which stays until it's deleted. The page reads
	// the listing again once it's made.
	interface Props {
		scope: Scope;
		/** The folder shown: empty for the scope's root. */
		path: string;
	}

	let { scope, path }: Props = $props();

	let open = $state(false);
	let name = $state('');
	let pending = $state(false);
	let error = $state<string | null>(null);

	async function create(e: SubmitEvent) {
		e.preventDefault();
		const target = name.trim();
		error = target.includes('/')
			? 'Enter one folder name, without slashes'
			: modelNameError(target);
		if (error) return;
		pending = true;
		try {
			await modelCall(modelsApi(scope, '-/folders'), {
				method: 'POST',
				body: { path: path ? `${path}/${target}` : target }
			});
			open = false;
			await invalidateAll();
		} catch (err) {
			error = (err as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

<Button variant="quiet" size="bar" onclick={() => (open = true)}>
	<FolderPlusIcon /> <span class="max-sm:sr-only">New folder</span>
</Button>
<Dialog.Root
	bind:open
	onOpenChangeComplete={(o) => {
		if (o) return;
		name = '';
		error = null;
	}}
>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>New folder</Dialog.Title>
		</Dialog.Header>
		<form class="grid gap-4" onsubmit={create}>
			<div class="grid gap-2">
				<Label for="model-folder-name">Name</Label>
				<Input
					id="model-folder-name"
					bind:value={name}
					autocomplete="off"
					spellcheck="false"
					autofocus
					aria-invalid={!!error}
				/>
			</div>
			<FormError {error} />
			<Dialog.Footer>
				<Button type="submit" {pending}>Create folder</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
