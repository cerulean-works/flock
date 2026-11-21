<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { datasetCall, datasetRef, datasetsApi, pathError, refHref } from '#lib/datasets.ts';
	import { forkPath } from '#lib/forks.ts';
	import type { Scope } from '#lib/scope.ts';
	import FormError from '../FormError.svelte';
	import ScopePicker from '../ScopePicker.svelte';
	import VersionSelect from './VersionSelect.svelte';

	interface Props {
		open: boolean;
		/** The dataset's scope. */
		scope: Scope;
		/** The dataset to fork. */
		path: string;
		/** The version to fork: the one shown on its page, or null for the latest. */
		version?: number | null;
		/** The org's projects: a fork may go to another scope (023 with 022). */
		projects?: readonly string[];
	}

	let { open = $bindable(), scope, path, version = null, projects = [] }: Props = $props();

	let to = $derived(forkPath(path));
	/** Where the fork goes: the dataset's own scope unless another is picked. */
	let toProject = $derived(scope.project);
	const picking = $derived(projects.length > 0 || scope.project !== null);
	let at = $derived(version === null ? '' : String(version));
	let error = $state<string | null>(null);
	let pending = $state(false);

	async function submit() {
		const target = to.trim().replace(/^\/+|\/+$/g, '');
		const same = toProject === scope.project && target === path;
		const invalid = same ? 'Choose a new path for the fork' : pathError(target);
		if (invalid) {
			error = invalid;
			return;
		}
		error = null;
		pending = true;
		try {
			// Both by their names across scopes (`project:path` for a project's).
			const made = await datasetCall<{ path: string }>(datasetsApi(scope.org, 'fork'), {
				method: 'POST',
				body: {
					path: datasetRef(scope, path),
					version: at.trim() || null,
					to: datasetRef({ org: scope.org, project: toProject }, target)
				}
			});
			toast.success(`Forked to ${made.path}`);
			open = false;
			await goto(refHref(scope.org, made.path));
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
		to = forkPath(path);
		toProject = scope.project;
		at = version === null ? '' : String(version);
		error = null;
	}}
>
	<Dialog.Content class="sm:max-w-lg" showCloseButton={!pending}>
		<Dialog.Header>
			<Dialog.Title class="break-all">Fork {path}</Dialog.Title>
			<Dialog.Description>
				The fork starts with this version’s rows and can be edited without changing {path}.
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
				<Label for="fork-version">Version</Label>
				<VersionSelect id="fork-version" {scope} {path} bind:value={at} disabled={pending} />
			</div>
			{#if picking}
				<div class="grid gap-2">
					<Label for="fork-scope">Project</Label>
					<ScopePicker id="fork-scope" bind:value={toProject} {projects} disabled={pending} />
				</div>
			{/if}
			<div class="grid gap-2">
				<Label for="fork-path">New path</Label>
				<Input
					id="fork-path"
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
					{#if pending}<LoaderCircleIcon class="animate-spin" /> Forking…{:else}Fork{/if}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
