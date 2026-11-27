<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { nameOf, parentOf } from '#lib/datasets.ts';
	import { modelCall, modelNameError, modelsApi } from '#lib/models.ts';
	import { type Scope, scopeLabel } from '#lib/scope.ts';
	import AnchoredForm from '../AnchoredForm.svelte';
	import FolderPicker from '../FolderPicker.svelte';
	import FormError from '../FormError.svelte';
	import ScopePicker from '../ScopePicker.svelte';

	// A model folder's row menu (D158): rename it, move it with its models (to another project
	// too, D166), or delete it once it's empty. A rename is one field, under the ⋯ menu.
	interface Props {
		open: boolean;
		action: 'rename' | 'move' | 'delete';
		/** The ⋯ menu a rename opens from. */
		anchor?: HTMLElement | null;
		scope: Scope;
		/** The folder's path. */
		path: string;
		/** How many models are under it. */
		models: number;
		/** The org's projects, for the Move scope picker. */
		projects?: readonly string[];
	}

	let {
		open = $bindable(),
		action,
		anchor = null,
		scope,
		path,
		models,
		projects = []
	}: Props = $props();

	let name = $derived(nameOf(path));
	let to = $derived(parentOf(path));
	let toProject = $derived(scope.project);
	let error = $state<string | null>(null);
	let pending = $state(false);
	/** Whether a rename is shown: kept while it closes, as the list clears its action at once. */
	let renaming = $state(false);
	$effect.pre(() => {
		if (open) renaming = action === 'rename';
	});

	const dest = $derived<Scope>({ org: scope.org, project: toProject });
	const target = $derived(to.trim().replace(/^\/+|\/+$/g, ''));
	const picking = $derived(projects.length > 0 || scope.project !== null);

	function reset() {
		name = nameOf(path);
		to = parentOf(path);
		toProject = scope.project;
		error = null;
	}

	async function submit() {
		let body: { path: string; to?: string; project?: string | null };
		if (action === 'delete') {
			body = { path };
		} else {
			const last = action === 'rename' ? name.trim() : nameOf(path);
			if (action === 'rename' && last.includes('/')) {
				error = 'Enter one folder name, without slashes';
				return;
			}
			const folder = action === 'rename' ? parentOf(path) : target;
			const newPath = folder ? `${folder}/${last}` : last;
			error = modelNameError(newPath);
			if (error) return;
			if (newPath === path && toProject === scope.project) {
				error = action === 'rename' ? 'Enter a new name' : 'It’s already here';
				return;
			}
			body = { path, to: newPath, ...(toProject !== scope.project ? { project: toProject } : {}) };
		}
		error = null;
		pending = true;
		try {
			if (action === 'delete') {
				await modelCall(modelsApi(scope, '-/folders', { path }), { method: 'DELETE' });
				toast.success(`Deleted ${nameOf(path)}/`);
			} else {
				await modelCall(modelsApi(scope, '-/folders'), { method: 'PATCH', body });
				const where = body.to + (body.project !== undefined ? ` in ${scopeLabel(toProject)}` : '');
				toast.success(`${action === 'rename' ? 'Renamed' : 'Moved'} to ${where}`);
			}
			open = false;
			await invalidateAll();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			pending = false;
		}
	}
</script>

{#if renaming}
	<AnchoredForm
		bind:open
		{anchor}
		align="end"
		title="Rename {nameOf(path)}/"
		submit="Rename"
		{pending}
		{error}
		onsubmit={submit}
		onclosed={reset}
	>
		<Input
			bind:value={name}
			aria-label="New name"
			autocomplete="off"
			spellcheck="false"
			disabled={pending}
			aria-invalid={!!error}
		/>
		{#snippet note()}
			{#if models}Its models go with it. Code that loads them by their old names stops working.{/if}
		{/snippet}
	</AnchoredForm>
{:else}
	<Dialog.Root
		bind:open={() => open, (o) => (open = pending ? true : o)}
		onOpenChangeComplete={(o) => o || reset()}
	>
		<Dialog.Content class="sm:max-w-md" showCloseButton={!pending}>
			<Dialog.Header>
				<Dialog.Title class="break-all">
					{action === 'rename' ? 'Rename' : action === 'move' ? 'Move' : 'Delete'}
					{nameOf(path)}/
				</Dialog.Title>
				{#if action === 'delete'}
					<Dialog.Description>
						{models
							? 'Only an empty folder can be deleted. Move or delete the models in it first.'
							: 'The folder has no models, so nothing else is deleted.'}
					</Dialog.Description>
				{:else if models}
					<Dialog.Description>
						Its models go with it. Code that loads them by their old names stops working.
					</Dialog.Description>
				{/if}
			</Dialog.Header>
			<form
				class="grid gap-4"
				onsubmit={(e) => {
					e.preventDefault();
					void submit();
				}}
			>
				{#if action === 'move'}
					{#if picking}
						<div class="grid gap-2">
							<Label for="move-folder-scope">Project</Label>
							<ScopePicker
								id="move-folder-scope"
								bind:value={toProject}
								{projects}
								disabled={pending}
							/>
						</div>
					{/if}
					<div class="grid gap-2">
						<Label for="move-folder-to">Into folder</Label>
						<FolderPicker
							id="move-folder-to"
							bind:value={to}
							scope={dest}
							kind="model"
							exclude={(p) =>
								toProject === scope.project && (p === path || p.startsWith(`${path}/`))}
							disabled={pending}
						/>
					</div>
				{/if}
				<FormError {error} />
				<Dialog.Footer>
					<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}>
						Cancel
					</Button>
					<Button
						type="submit"
						variant={action === 'delete' ? 'destructive' : 'default'}
						disabled={pending || (action === 'delete' && models > 0)}
						{pending}
					>
						{action === 'rename' ? 'Rename' : action === 'move' ? 'Move' : 'Delete'}
					</Button>
				</Dialog.Footer>
			</form>
		</Dialog.Content>
	</Dialog.Root>
{/if}
