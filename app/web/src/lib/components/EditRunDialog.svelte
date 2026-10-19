<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Textarea } from '#lib/components/ui/textarea/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { gpus } from '#lib/utils.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import FormError from './FormError.svelte';

	interface Props {
		/**
		 * The run's GPUs, to correct them: those in effect and those wandb detected (set by hand
		 * where they differ). Without them the dialog leaves GPUs alone.
		 */
		gpu?: {
			type: string | null;
			count: number | null;
			detected: { type: string | null; count: number | null };
		};
	}

	let { gpu }: Props = $props();

	const ws = getWorkspace();

	// Closes here first; `ws.dialog` is cleared once the closing animation is over, so the
	// dialog keeps its content while it fades.
	let open = $derived(ws.dialog?.kind === 'edit');
	const mutation = new Mutation({
		runs: true,
		success: () => 'Run saved',
		done: () => (open = false)
	});

	const text = (v: unknown) => (v == null ? '' : String(v));

	// The run's values when the dialog opened. Untracked, so live updates to the table don't
	// overwrite what is being typed.
	const run = $derived.by(() => {
		const d = ws.dialog;
		if (d?.kind !== 'edit') return null;
		return untrack(() => {
			const row = ws.rowsOf(d.ids)[0];
			if (row === undefined) return null;
			const tags = ws.cell(row, 'tags');
			return {
				id: d.ids[0],
				name: ws.nameOf(row),
				tags: Array.isArray(tags) ? tags.join(', ') : '',
				group: text(ws.cell(row, 'group')),
				jobType: text(ws.cell(row, 'job_type')),
				notes: text(ws.cell(row, 'notes'))
			};
		});
	});
</script>

<Dialog.Root
	bind:open
	onOpenChangeComplete={(o) => {
		if (o) return;
		ws.dialog = null;
		mutation.reset();
	}}
>
	<Dialog.Content class="sm:max-w-lg">
		{#if run}
			<Dialog.Header>
				<Dialog.Title>Edit run</Dialog.Title>
				<Dialog.Description class="font-mono text-[12.5px]">{run.id}</Dialog.Description>
			</Dialog.Header>
			<form method="POST" action="?/edit" class="grid gap-4" use:enhance={mutation.enhance}>
				<input type="hidden" name="run" value={run.id} />
				<div class="grid gap-2">
					<Label for="run-name">Name</Label>
					<Input
						id="run-name"
						name="display_name"
						value={run.name}
						placeholder={run.id}
						autocomplete="off"
					/>
				</div>
				<div class="grid gap-2">
					<Label for="run-tags">Tags</Label>
					<Input
						id="run-tags"
						name="tags"
						value={run.tags}
						placeholder="baseline, lr-sweep"
						autocomplete="off"
					/>
					<p class="text-xs text-muted-foreground">Separate tags with commas or spaces.</p>
				</div>
				<div class="grid grid-cols-2 gap-3">
					<div class="grid gap-2">
						<Label for="run-group">Group</Label>
						<Input id="run-group" name="group" value={run.group} autocomplete="off" />
					</div>
					<div class="grid gap-2">
						<Label for="run-job-type">Job type</Label>
						<Input id="run-job-type" name="job_type" value={run.jobType} autocomplete="off" />
					</div>
				</div>
				{#if gpu}
					{@const detected = gpus(gpu.detected.type, gpu.detected.count)}
					<div class="grid grid-cols-[minmax(0,1fr)_7rem] gap-3">
						<div class="grid gap-2">
							<Label for="run-gpu-type">GPU type</Label>
							<Input
								id="run-gpu-type"
								name="gpu_type"
								value={gpu.type !== gpu.detected.type ? (gpu.type ?? '') : ''}
								placeholder={gpu.detected.type ?? 'e.g. H100'}
								autocomplete="off"
							/>
						</div>
						<div class="grid gap-2">
							<Label for="run-gpu-count">GPUs</Label>
							<Input
								id="run-gpu-count"
								name="gpu_count"
								type="number"
								min="0"
								step="1"
								value={gpu.count !== gpu.detected.count ? (gpu.count ?? '') : ''}
								placeholder={gpu.detected.count == null ? '' : String(gpu.detected.count)}
							/>
						</div>
					</div>
					<p class="-mt-2 text-xs text-muted-foreground">
						For its cost. Leave blank for what wandb detected{detected ? ` (${detected})` : ''}.
					</p>
				{/if}
				<div class="grid gap-2">
					<Label for="run-notes">Notes</Label>
					<Textarea id="run-notes" name="notes" value={run.notes} rows={3} />
				</div>
				<p class="text-xs text-muted-foreground">The run’s script can overwrite these.</p>
				<FormError error={mutation.error} ids={mutation.ids} />
				<Dialog.Footer>
					<Dialog.Close>
						{#snippet child({ props })}
							<Button {...props} variant="outline">Cancel</Button>
						{/snippet}
					</Dialog.Close>
					<Button type="submit" disabled={mutation.pending}>
						{mutation.pending ? 'Saving…' : 'Save'}
					</Button>
				</Dialog.Footer>
			</form>
		{/if}
	</Dialog.Content>
</Dialog.Root>
