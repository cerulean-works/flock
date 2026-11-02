<script lang="ts">
	import { enhance, type SubmitFunction } from '$app/forms';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Textarea } from '#lib/components/ui/textarea/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { ProjectInfo, Visibility } from '#lib/types.ts';
	import { formText, projectNameError } from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import FormError from './FormError.svelte';

	interface Props {
		open: boolean;
		entity: string;
		/** The project to edit; creates one when omitted. */
		project?: ProjectInfo | null;
		/** Where the form is: a rename from the project's own page follows it to its new URL. */
		from?: 'project' | 'list';
	}

	let { open = $bindable(), entity, project = null, from = 'list' }: Props = $props();

	const mutation = new Mutation({
		validate: (form) => projectNameError(formText(form, 'name')),
		success: () => (project ? 'Project saved' : 'Project created'),
		done: () => (open = false)
	});

	// Org admins edit everything; a member who created the project only its visibility (D118).
	const admin = $derived(page.data.shell?.access === 'admin');
	const creator = $derived(
		project?.created_by != null && project.created_by === page.data.shell?.me?.id
	);
	const editable = $derived(!project || admin);
	const publisher = $derived(admin || creator);

	// Live clients address their runs by project name, so Flock refuses to rename a
	// project while any of its runs is running.
	const locked = $derived(!!project?.active_run_count);
	const renamable = $derived(editable && !locked);
	const visibilities: { value: Visibility; label: string; hint: string }[] = $derived([
		{ value: 'private', label: 'Private', hint: `Only members of ${entity} can see it.` },
		{
			value: 'public',
			label: 'Public',
			hint: 'Anyone with the link can see its runs, without signing in.'
		}
	]);
	let visibility = $derived<Visibility>(project?.visibility ?? 'private');

	// Making it public is confirmed first, then the whole form goes from the confirmation.
	let publishing = $state(false);
	let fields = $state<Record<string, string>>({});
	const submit: SubmitFunction = (input) => {
		const { formData, cancel } = input;
		// Sent only when changed: an untouched choice mustn't undo another admin's change, or
		// make a project private whose visibility this dialog never knew.
		if (formData.get('visibility') === (project?.visibility ?? 'private'))
			formData.delete('visibility');
		const going = project?.visibility !== 'public' && formData.get('visibility') === 'public';
		if (!going || projectNameError(formText(formData, 'name'))) return mutation.enhance(input);
		cancel();
		fields = Object.fromEntries([...formData].map(([k, v]) => [k, String(v)]));
		publishing = true;
	};
</script>

<Dialog.Root bind:open onOpenChange={(o) => o || mutation.reset()}>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>{project ? 'Edit project' : 'New project'}</Dialog.Title>
			<Dialog.Description>
				{#if project}
					{entity} / {project.name}
				{:else}
					In {entity}.
				{/if}
			</Dialog.Description>
		</Dialog.Header>
		<form
			method="POST"
			action="{entityHref(entity)}?/{project ? 'update' : 'create'}"
			class="grid gap-4"
			use:enhance={submit}
		>
			{#if project}
				<input type="hidden" name="project" value={project.name} />
				<input type="hidden" name="from" value={from} />
			{/if}
			<div class="grid gap-2">
				<Label for="project-name">Name</Label>
				{#if !renamable}
					<!-- A disabled field isn't submitted: send the current name instead. -->
					<input type="hidden" name="name" value={project?.name} />
				{/if}
				<Input
					id="project-name"
					name={renamable ? 'name' : undefined}
					value={project?.name ?? ''}
					autocomplete="off"
					spellcheck="false"
					required
					maxlength={128}
					disabled={!renamable}
					aria-invalid={!!mutation.error}
				/>
				{#if !editable}
					<p class="text-xs text-muted-foreground">
						Only admins of {entity} can rename it or change its description.
					</p>
				{:else if locked}
					<p class="text-xs text-muted-foreground">
						Can’t be renamed while {project?.active_run_count}
						{project?.active_run_count === 1 ? 'run is' : 'runs are'} running.
					</p>
				{/if}
			</div>
			<div class="grid gap-2">
				<Label for="project-description">
					Description <span class="font-normal text-muted-foreground">optional</span>
				</Label>
				<Textarea
					id="project-description"
					name="description"
					value={project?.description ?? ''}
					rows={3}
					disabled={!editable}
				/>
			</div>
			{#if project && publisher}
				<fieldset class="grid gap-2">
					<legend class="mb-2 text-sm leading-none font-medium">Visibility</legend>
					{#each visibilities as v (v.value)}
						<label
							class="flex cursor-pointer items-start gap-3 rounded-lg border border-input px-3 py-2.5 has-checked:border-ring has-checked:bg-muted/60 has-focus-visible:ring-3 has-focus-visible:ring-ring/50"
						>
							<input
								type="radio"
								name="visibility"
								value={v.value}
								bind:group={visibility}
								class="mt-0.5 accent-primary outline-none"
							/>
							<span class="grid gap-0.5">
								<span class="text-sm font-medium">{v.label}</span>
								<span class="text-xs text-muted-foreground">{v.hint}</span>
							</span>
						</label>
					{/each}
				</fieldset>
			{/if}
			<FormError error={mutation.error} />
			<Dialog.Footer>
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props} variant="outline">Cancel</Button>
					{/snippet}
				</Dialog.Close>
				<Button type="submit" disabled={mutation.pending}>
					{#if mutation.pending}Saving…{:else if project}Save{:else}Create project{/if}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>

{#if project && publisher}
	<ConfirmDialog
		bind:open={publishing}
		action="{entityHref(entity)}?/update"
		{fields}
		title="Make {project.name} public?"
		description="Anyone with the link will be able to see every run in this project, including configs, files and logs. Check that none of them hold secrets."
		confirm="Make public"
		pending="Saving…"
		success="{project.name} is now public"
		done={() => (open = false)}
	/>
{/if}
