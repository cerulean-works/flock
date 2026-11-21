<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { nameOf, parentOf } from '#lib/datasets.ts';
	import { modelApi, modelCall, modelNameError } from '#lib/models.ts';
	import { type Scope, scopeLabel } from '#lib/scope.ts';
	import type { ModelMove } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import FolderPicker from '../FolderPicker.svelte';
	import FormError from '../FormError.svelte';
	import ItemFailures from '../ItemFailures.svelte';
	import LeavingProject from '../LeavingProject.svelte';
	import ScopePicker from '../ScopePicker.svelte';

	interface Props {
		open: boolean;
		/** The scope shown, where the models are. */
		scope: Scope;
		/** The folder shown, where the destination starts. */
		from: string;
		/** The models' paths. */
		names: readonly string[];
		/** The org's projects, for the scope picker (D166). */
		projects?: readonly string[];
		/** Once every model was asked: those that moved leave the list. */
		onmoved: (results: ModelMove[], to: Scope) => void | Promise<void>;
	}

	let { open = $bindable(), scope, from, names, projects = [], onmoved }: Props = $props();

	// Reset once the dialog has closed, so its text stays put while it animates closed.
	let to = $derived(from);
	/** The destination's project, the scope shown at first; null for global. */
	let toProject = $derived(scope.project);
	let error = $state<string | null>(null);
	let failures = $state<{ path: string; error: string | null }[]>([]);
	let pending = $state(false);

	function reset() {
		to = from;
		toProject = scope.project;
		error = null;
		failures = [];
	}

	/** The destination as typed, without slashes at either end. */
	const target = $derived(to.trim().replace(/^\/+|\/+$/g, ''));
	const dest = $derived<Scope>({ org: scope.org, project: toProject });
	const across = $derived(toProject !== scope.project);
	/** Each model keeps its last segment (D166). */
	const moveTo = (name: string) => (target ? `${target}/${nameOf(name)}` : nameOf(name));
	const label = $derived(names.length === 1 ? nameOf(names[0]) : count(names.length, 'model'));

	// Models move one request each (D166), in turn; one refused leaves the rest going.
	async function submit() {
		const invalid = target ? modelNameError(target) : null;
		if (invalid) {
			error = invalid;
			return;
		}
		if (!across && names.every((n) => parentOf(n) === target)) {
			error = names.length === 1 ? 'It’s already in this folder' : 'They’re already in this folder';
			return;
		}
		error = null;
		pending = true;
		const results: ModelMove[] = [];
		for (const name of names) {
			const path = moveTo(name);
			try {
				await modelCall(modelApi(scope, name), {
					method: 'PATCH',
					body: { name: path, project: toProject }
				});
				results.push({ name, to: path, moved: true, error: null });
			} catch (e) {
				results.push({ name, to: path, moved: false, error: (e as Error).message });
			}
		}
		const moved = results.filter((r) => r.moved).length;
		failures = results.filter((r) => !r.moved).map((r) => ({ path: r.name, error: r.error }));
		try {
			await onmoved(results, dest);
		} finally {
			pending = false;
		}
		if (moved) {
			const where = `${target || scopeLabel(toProject)}${across && target ? ` in ${scopeLabel(toProject)}` : ''}`;
			toast.success(`Moved ${moved === names.length ? label : count(moved, 'model')} to ${where}`);
		}
		if (!failures.length) open = false;
	}
</script>

<!-- While the models move, the dialog can't be closed: the requests decide how it ends. -->
<Dialog.Root
	bind:open={() => open, (o) => (open = pending ? true : o)}
	onOpenChangeComplete={(o) => o || reset()}
>
	<Dialog.Content
		class="sm:max-w-lg"
		showCloseButton={!pending}
		escapeKeydownBehavior={pending ? 'ignore' : 'close'}
		interactOutsideBehavior={pending ? 'ignore' : 'close'}
	>
		<Dialog.Header>
			<Dialog.Title class="break-all">Move {label}</Dialog.Title>
			<Dialog.Description>
				To another folder, here or in another project. Code that loads {names.length === 1
					? 'it'
					: 'them'} by the old name stops working.
			</Dialog.Description>
		</Dialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (!failures.length) void submit();
			}}
		>
			{#if failures.length}
				<ItemFailures {failures} summary="{count(failures.length, 'model')} didn’t move." />
			{:else}
				<div class="grid gap-2">
					<Label for="move-models-scope">Project</Label>
					<ScopePicker
						id="move-models-scope"
						bind:value={toProject}
						{projects}
						disabled={pending}
					/>
				</div>
				<div class="grid gap-2">
					<Label for="move-models-to">Folder</Label>
					<FolderPicker
						id="move-models-to"
						bind:value={to}
						scope={dest}
						kind="model"
						disabled={pending}
					/>
				</div>
				{#if scope.project !== null && toProject === null}
					<LeavingProject org={scope.org} paths={names.map(moveTo)} folder={target} />
				{/if}
			{/if}
			<FormError {error} />
			<Dialog.Footer>
				{#if failures.length}
					<Button type="button" onclick={() => (open = false)}>Close</Button>
				{:else}
					<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}>
						Cancel
					</Button>
					<Button type="submit" disabled={pending}>
						{#if pending}<LoaderCircleIcon class="animate-spin" /> Moving…{:else}Move{/if}
					</Button>
				{/if}
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
