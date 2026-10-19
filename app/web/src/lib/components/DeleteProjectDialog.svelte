<script lang="ts">
	import { enhance } from '$app/forms';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { ProjectInfo } from '#lib/types.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import FormError from './FormError.svelte';

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
		</AlertDialog.Header>
		<form
			method="POST"
			action="{entityHref(entity)}?/delete"
			class="grid gap-4"
			use:enhance={mutation.enhance}
		>
			<input type="hidden" name="project" value={project.name} />
			<input type="hidden" name="from" value={from} />
			<div class="grid gap-2">
				<Label for="delete-project-name">
					Type <span class="font-mono text-[12.5px]">{project.name}</span> to confirm
				</Label>
				<Input id="delete-project-name" bind:value={typed} autocomplete="off" spellcheck="false" />
			</div>
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
