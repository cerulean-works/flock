<script lang="ts">
	import { enhance } from '$app/forms';
	import type { ResolvedPathname } from '$app/types';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { LIVE_STATES } from '#lib/types.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import FormError from './FormError.svelte';

	interface Props {
		/** Where to go once the runs are deleted, rather than reload this page. */
		leave?: () => ResolvedPathname;
	}

	let { leave }: Props = $props();

	const ws = getWorkspace();

	let open = $derived(ws.dialog?.kind === 'delete');
	const ids = $derived(ws.dialog?.kind === 'delete' ? ws.dialog.ids : []);
	const rows = $derived(ws.rowsOf(ids));
	const live = $derived(rows.filter((r) => LIVE_STATES.has(ws.stateOf(r))).length);
	let force = $derived.by(() => {
		void ws.dialog;
		return false;
	});

	const runs = (n: number) => `${n.toLocaleString('en-US')} ${n === 1 ? 'run' : 'runs'}`;
	const nameOf = (id: string) => ws.page.runs.find((r) => r.id === id)?.name ?? id;

	const mutation = new Mutation<{ count: number }>({
		runs: true,
		submit: () => ids.forEach((id) => ws.removing.add(id)),
		leave: () => leave?.(),
		success: ({ count }) => `Deleted ${runs(count)}`,
		done: () => {
			for (const id of ids) ws.selected.delete(id);
			open = false;
		}
	});
</script>

<AlertDialog.Root
	bind:open
	onOpenChangeComplete={(o) => {
		if (o) return;
		ws.dialog = null;
		mutation.reset();
	}}
>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title>
				Delete {ids.length === 1 ? nameOf(ids[0]) : runs(ids.length)}?
			</AlertDialog.Title>
			<AlertDialog.Description>
				Everything {ids.length === 1 ? 'it' : 'they'} logged is deleted. There is no undo.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form method="POST" action="?/delete" class="grid gap-4" use:enhance={mutation.enhance}>
			{#each ids as id (id)}
				<input type="hidden" name="run" value={id} />
			{/each}
			{#if live}
				<div class="flex items-start gap-2.5">
					<Checkbox id="delete-runs-force" name="force" bind:checked={force} class="mt-0.5" />
					<Label for="delete-runs-force" class="block leading-snug font-normal">
						{live === ids.length
							? `${ids.length === 1 ? 'It is' : 'They are'} still running. Delete anyway.`
							: `Also delete the ${runs(live)} still running.`}
						<span class="text-muted-foreground"
							>{live === 1
								? 'Its script keeps going, but what it logs is dropped.'
								: 'Their scripts keep going, but what they log is dropped.'}</span
						>
					</Label>
				</div>
			{/if}
			<FormError error={mutation.error} ids={mutation.ids} label={nameOf} />
			<AlertDialog.Footer>
				<AlertDialog.Cancel>Cancel</AlertDialog.Cancel>
				<Button
					type="submit"
					variant="destructive"
					disabled={(live > 0 && !force) || mutation.pending}
				>
					{mutation.pending
						? 'Deleting…'
						: ids.length === 1
							? 'Delete run'
							: `Delete ${runs(ids.length)}`}
				</Button>
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
