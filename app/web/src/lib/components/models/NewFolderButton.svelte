<script lang="ts">
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { modelCall, modelNameError, modelsApi } from '#lib/models.ts';
	import type { Scope } from '#lib/scope.ts';
	import AnchoredForm from '../AnchoredForm.svelte';

	// An empty folder of a scope's models (D158), which stays until it's deleted. The page reads
	// the listing again once it's made.
	interface Props {
		scope: Scope;
		/** The folder shown: empty for the scope's root. */
		path: string;
	}

	let { scope, path }: Props = $props();

	let open = $state(false);
	let button = $state<HTMLElement | null>(null);
	let name = $state('');
	let pending = $state(false);
	let error = $state<string | null>(null);

	async function create() {
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

<Button
	variant={open ? 'quiet-on' : 'quiet'}
	size="bar"
	bind:ref={button}
	onclick={() => (open = !open)}
>
	<FolderPlusIcon /> <span class="max-sm:sr-only">New folder</span>
</Button>
<AnchoredForm
	bind:open
	anchor={button}
	title="New folder"
	submit="Create folder"
	{pending}
	{error}
	onsubmit={create}
	onclosed={() => {
		name = '';
		error = null;
	}}
>
	<Input
		bind:value={name}
		aria-label="Name"
		placeholder="Name"
		autocomplete="off"
		spellcheck="false"
		aria-invalid={!!error}
	/>
</AnchoredForm>
