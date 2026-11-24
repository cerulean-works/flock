<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import {
		DATASETS_BUCKET,
		DatasetError,
		type DatasetItem,
		datasetCall,
		datasetsApi,
		itemName,
		nameOf,
		parentOf,
		pathError,
		within
	} from '#lib/datasets.ts';
	import { type Scope, scopeLabel } from '#lib/scope.ts';
	import type { MoveResult } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import FolderPicker from '../FolderPicker.svelte';
	import FormError from '../FormError.svelte';
	import ItemFailures from '../ItemFailures.svelte';
	import LeavingProject from '../LeavingProject.svelte';
	import ScopePicker from '../ScopePicker.svelte';

	interface Props {
		open: boolean;
		/** The scope shown, where the items are. */
		scope: Scope;
		/** The folder shown, where the destination starts. */
		from: string;
		items: readonly DatasetItem[];
		/** The org's projects, for the scope picker (D166). */
		projects?: readonly string[];
		/** Once the request answered: the items that moved leave the list. */
		onmoved: (results: MoveResult[], to: Scope) => void | Promise<void>;
	}

	let { open = $bindable(), scope, from, items, projects = [], onmoved }: Props = $props();

	// Reset once the dialog has closed, so its text stays put while it animates closed.
	let to = $derived(from);
	/** The destination's project, the scope shown at first; null for global. */
	let toProject = $derived(scope.project);
	let error = $state<string | null>(null);
	let command = $state<string | null>(null);
	let failures = $state<MoveResult[]>([]);
	let pending = $state(false);

	function reset() {
		to = from;
		toProject = scope.project;
		error = null;
		command = null;
		failures = [];
	}

	/** The destination as typed, without slashes at either end. */
	const target = $derived(to.trim().replace(/^\/+|\/+$/g, ''));
	const dest = $derived<Scope>({ org: scope.org, project: toProject });
	const across = $derived(toProject !== scope.project);
	const picking = $derived(projects.length > 0 || scope.project !== null);

	const name = $derived(items.length === 1 ? itemName(items[0]) : count(items.length, 'item'));
	/** Where they land, as the toast says it. */
	const where = $derived(
		`${target || (toProject ?? DATASETS_BUCKET)}${across && target ? ` in ${scopeLabel(toProject)}` : ''}`
	);

	async function submit() {
		const invalid = target ? pathError(target) : null;
		if (invalid) {
			error = invalid;
			return;
		}
		if (!across && items.every((i) => parentOf(i.path) === target)) {
			error = items.length === 1 ? 'It’s already in this folder' : 'They’re already in this folder';
			return;
		}
		error = null;
		command = null;
		pending = true;
		try {
			const { results } = await datasetCall<{ results: MoveResult[] }>(datasetsApi(scope, 'move'), {
				method: 'POST',
				body: {
					paths: items.map((i) => i.path),
					to: target,
					...(across ? { to_project: toProject } : {})
				}
			});
			const moved = results.filter((r) => r.moved).length;
			failures = results.filter((r) => !r.moved);
			await onmoved(results, dest);
			if (moved)
				toast.success(`Moved ${moved === items.length ? name : count(moved, 'item')} to ${where}`);
			if (!failures.length) open = false;
		} catch (e) {
			error = (e as Error).message;
			command =
				e instanceof DatasetError && typeof e.body.command === 'string' ? e.body.command : null;
		} finally {
			pending = false;
		}
	}
</script>

<!-- While the items move, the dialog can't be closed: the request decides how it ends. -->
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
			<Dialog.Title class="break-all">Move {name}</Dialog.Title>
			<Dialog.Description>
				{picking
					? 'To another folder, here or in another project.'
					: `To another folder in ${DATASETS_BUCKET}.`}
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
				<ItemFailures {failures} summary="{count(failures.length, 'item')} didn’t move." />
			{:else}
				{#if picking}
					<div class="grid gap-2">
						<Label for="move-datasets-scope">Project</Label>
						<ScopePicker
							id="move-datasets-scope"
							bind:value={toProject}
							{projects}
							disabled={pending}
						/>
					</div>
				{/if}
				<div class="grid gap-2">
					<Label for="move-datasets-to">Folder</Label>
					<FolderPicker
						id="move-datasets-to"
						bind:value={to}
						scope={dest}
						kind="dataset"
						exclude={(path) => !across && items.some((i) => i.folder && within(path, i.path))}
						disabled={pending}
					/>
				</div>
				{#if scope.project !== null && toProject === null}
					<LeavingProject
						org={scope.org}
						paths={items.map((i) => (target ? `${target}/${nameOf(i.path)}` : nameOf(i.path)))}
						folder={target}
					/>
				{/if}
			{/if}
			<FormError {error} />
			{#if command}<CopyBlock code={command} lang="bash" />{/if}
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
