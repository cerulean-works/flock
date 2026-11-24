<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import { enhance, type SubmitFunction } from '$app/forms';
	import BoxIcon from '@lucide/svelte/icons/box';
	import DatabaseIcon from '@lucide/svelte/icons/database';
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
	// With datasets or models to lose, confirming first lists them on a second screen.
	let step = $derived.by<'confirm' | 'review'>(() => {
		void open;
		return 'confirm';
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
	const items = $derived(storage?.items ?? []);
	// Enter in the name field submits the form too: with things to lose, that only moves on to
	// the list, and deleting takes the list's own button.
	const submit: SubmitFunction = (input) => {
		if (items.length && !reviewing) {
			input.cancel();
			if (ready) step = 'review';
			return;
		}
		return mutation.enhance(input);
	};
	const reviewing = $derived(step === 'review' && items.length > 0);
</script>

<AlertDialog.Root bind:open onOpenChange={(o) => o || mutation.reset()}>
	<AlertDialog.Content class="sm:max-w-md">
		{#if reviewing}
			<AlertDialog.Header>
				<AlertDialog.Title>These go with {project.name}</AlertDialog.Title>
				<AlertDialog.Description>
					Deleting the project deletes {count(items.length, 'item')} ({bytes(storage?.bytes ?? 0)})
					for good. Go back to move anything you want to keep.
				</AlertDialog.Description>
			</AlertDialog.Header>
			<ul
				class="-mx-1 max-h-72 overflow-y-auto rounded-md border border-divider"
				aria-label="Datasets and models deleted with the project"
			>
				{#each items as item (`${item.kind}/${item.path}`)}
					{@const Icon = item.kind === 'dataset' ? DatabaseIcon : BoxIcon}
					<li
						class="flex items-center gap-2.5 border-b border-divider px-3 py-2 text-[13.5px] last:border-0"
					>
						<Icon class="size-3.5 flex-none text-faint" aria-label={item.kind} />
						<span class="min-w-0 flex-1 truncate font-mono text-[12.5px]" {@attach hint(item.path)}
							>{item.path}</span
						>
						<span class="flex-none text-xs text-muted-foreground tabular-nums"
							>{bytes(item.bytes)}</span
						>
					</li>
				{/each}
			</ul>
		{/if}
		<AlertDialog.Header class={reviewing ? 'hidden' : undefined}>
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
					you want to keep to Workspace first, from its
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
			use:enhance={submit}
		>
			<input type="hidden" name="project" value={project.name} />
			<input type="hidden" name="from" value={from} />
			<div class={['grid gap-4', reviewing && 'hidden']}>
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
			</div>
			<FormError error={mutation.error} />
			<AlertDialog.Footer>
				{#if reviewing}
					<Button type="button" variant="outline" onclick={() => (step = 'confirm')}>Back</Button>
				{:else}
					<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				{/if}
				{#if items.length && !reviewing}
					<Button
						type="button"
						variant="destructive"
						disabled={!ready}
						onclick={() => (step = 'review')}
					>
						Continue
					</Button>
				{:else}
					<Button
						type="submit"
						variant="destructive"
						disabled={!ready || mutation.pending}
						pending={mutation.pending}
					>
						{items.length ? `Delete project and ${count(items.length, 'item')}` : 'Delete project'}
					</Button>
				{/if}
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
