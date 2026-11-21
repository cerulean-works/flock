<script lang="ts">
	import { enhance } from '$app/forms';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { datasetsHref } from '#lib/datasets.ts';
	import { modelsHref } from '#lib/models.ts';
	import type { ProjectInfo, ProjectStorage } from '#lib/types.ts';
	import { bytes, count } from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import FormError from './FormError.svelte';
	import ConfirmField from './ConfirmField.svelte';

	interface Props {
		open: boolean;
		entity: string;
		project: ProjectInfo;
		/** Deleting from the project's own page goes back to the entity's projects. */
		from?: 'project' | 'list';
	}

	let { open = $bindable(), entity, project, from = 'list' }: Props = $props();

	const mutation = new Mutation({
		success: () => `Deleted ${project.name}`,
		done: () => (open = false)
	});

	// Typing the name is the confirmation; reset whenever the dialog opens.
	let typed = $derived.by(() => {
		void open;
		return '';
	});
	let force = $derived.by(() => {
		void open;
		return false;
	});
	const active = $derived(project.active_run_count);

	// What goes with the project (D167): its datasets and models, read when the dialog opens.
	// Without S3, or if the read fails, the dialog says nothing of them.
	let storage = $state<ProjectStorage | null>(null);
	$effect(() => {
		if (!open) return;
		let live = true;
		storage = null;
		const url = `/api/projects/${encodeURIComponent(entity)}/${encodeURIComponent(project.name)}/storage`;
		fetch(url)
			.then((res) => (res.ok ? (res.json() as Promise<ProjectStorage>) : null))
			.then((found) => {
				if (live) storage = found;
			})
			.catch(() => {});
		return () => {
			live = false;
		};
	});
	const kept = $derived(
		storage && (storage.datasets || storage.models)
			? [
					storage.datasets ? count(storage.datasets, 'dataset') : null,
					storage.models ? count(storage.models, 'model') : null
				]
					.filter(Boolean)
					.join(' and ')
			: null
	);
	const tabs = $derived.by(() => {
		const scope = { org: entity, project: project.name };
		return [
			storage?.datasets ? { label: 'Datasets', href: datasetsHref(scope) } : null,
			storage?.models ? { label: 'Models', href: modelsHref(scope) } : null
		].filter((t) => t !== null);
	});
	const ready = $derived(typed === project.name && (!active || force));
</script>

<AlertDialog.Root bind:open onOpenChange={(o) => o || mutation.reset()}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {project.name}?</AlertDialog.Title>
			<AlertDialog.Description>
				{#if project.run_count}
					Deletes its {project.run_count.toLocaleString('en-US')}
					{project.run_count === 1 ? 'run' : 'runs'} and everything they logged. There is no undo.
				{:else}
					It has no runs. There is no undo.
				{/if}
			</AlertDialog.Description>
			{#if storage && kept}
				<p class="text-sm text-muted-foreground" role="status">
					{kept} ({bytes(storage.bytes)})
					{storage.datasets + storage.models === 1 ? 'is' : 'are'} deleted with this project. Move anything
					you want to keep to Global first, from its
					{#each tabs as tab, i (tab.label)}{i ? ' and ' : ''}<a
							class="text-link underline-offset-3 hover:underline"
							href={tab.href}>{tab.label}</a
						>{/each}
					{tabs.length > 1 ? 'tabs' : 'tab'}.
				</p>
			{/if}
		</AlertDialog.Header>
		<form
			method="POST"
			action="{entityHref(entity)}?/delete"
			class="grid gap-4"
			use:enhance={mutation.enhance}
		>
			<input type="hidden" name="project" value={project.name} />
			<input type="hidden" name="from" value={from} />
			<ConfirmField id="delete-project-name" text={project.name} bind:value={typed} />
			{#if active}
				<div class="flex items-start gap-2.5">
					<Checkbox id="delete-project-force" name="force" bind:checked={force} class="mt-0.5" />
					<Label for="delete-project-force" class="block leading-snug font-normal">
						Also delete its {active}
						{active === 1 ? 'running run' : 'running runs'}.
						<span class="text-muted-foreground"
							>{active === 1
								? 'Its script keeps going, but what it logs is dropped.'
								: 'Their scripts keep going, but what they log is dropped.'}</span
						>
					</Label>
				</div>
			{/if}
			<FormError error={mutation.error} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				<Button type="submit" variant="destructive" disabled={!ready || mutation.pending}>
					{mutation.pending ? 'Deleting…' : 'Delete project'}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
