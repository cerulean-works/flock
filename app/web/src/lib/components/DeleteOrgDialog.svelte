<script lang="ts">
	import { enhance } from '$app/forms';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { OrgInfo } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import FormError from './FormError.svelte';

	let { open = $bindable(), org }: { open: boolean; org: OrgInfo } = $props();

	const mutation = new Mutation({ success: () => `Deleted ${org.name}` });

	// Typing the name is the confirmation; reset whenever the dialog opens.
	let typed = $derived.by(() => {
		void open;
		return '';
	});
	let force = $derived.by(() => {
		void open;
		return false;
	});
	// Flock refuses while runs are running, naming them: only then is `force` offered.
	const active = $derived(mutation.ids.length);
	const ready = $derived(typed === org.name && (!active || force));
</script>

<AlertDialog.Root bind:open onOpenChange={(o) => o || mutation.reset()}>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>Delete {org.name}?</AlertDialog.Title>
			<AlertDialog.Description>
				{#if org.project_count}
					Deletes its {count(org.project_count, 'project')} and their runs. Members keep their accounts.
					There is no undo.
				{:else}
					It has no projects. Members keep their accounts. There is no undo.
				{/if}
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form method="POST" action="?/delete" class="grid gap-4" use:enhance={mutation.enhance}>
			<div class="grid gap-2">
				<Label for="delete-org-name">
					Type <span class="font-mono text-[12.5px]">{org.name}</span> to confirm
				</Label>
				<Input id="delete-org-name" bind:value={typed} autocomplete="off" spellcheck="false" />
			</div>
			{#if active}
				<div class="flex items-start gap-2.5">
					<Checkbox id="delete-org-force" name="force" bind:checked={force} class="mt-0.5" />
					<Label for="delete-org-force" class="block leading-snug font-normal">
						Also delete its {active === 1 ? 'running run' : `${active} running runs`}.
						<span class="text-muted-foreground"
							>{active === 1
								? 'Its script keeps going, but what it logs is dropped.'
								: 'Their scripts keep going, but what they log is dropped.'}</span
						>
					</Label>
				</div>
			{:else}
				<FormError error={mutation.error} />
			{/if}
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				<Button type="submit" variant="destructive" disabled={!ready || mutation.pending}>
					{mutation.pending ? 'Deleting…' : 'Delete organization'}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
