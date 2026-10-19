<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Textarea } from '#lib/components/ui/textarea/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { ProjectInfo } from '#lib/types.ts';
	import { formText, projectNameError } from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
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

	// Live clients address their runs by project name, so Flock refuses to rename a
	// project while any of its runs is running.
	const locked = $derived(!!project?.active_run_count);
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
			use:enhance={mutation.enhance}
		>
			{#if project}
				<input type="hidden" name="project" value={project.name} />
				<input type="hidden" name="from" value={from} />
			{/if}
			<div class="grid gap-2">
				<Label for="project-name">Name</Label>
				{#if locked}
					<!-- A disabled field isn't submitted: send the current name instead. -->
					<input type="hidden" name="name" value={project?.name} />
				{/if}
				<Input
					id="project-name"
					name={locked ? undefined : 'name'}
					value={project?.name ?? ''}
					autocomplete="off"
					spellcheck="false"
					required
					maxlength={128}
					disabled={locked}
					aria-invalid={!!mutation.error}
				/>
				{#if locked}
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
				/>
			</div>
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
