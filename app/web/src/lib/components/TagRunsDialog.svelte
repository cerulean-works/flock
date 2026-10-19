<script lang="ts">
	import { untrack } from 'svelte';
	import XIcon from '@lucide/svelte/icons/x';
	import { enhance } from '$app/forms';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { formText, parseTags } from '#lib/utils.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import FormError from './FormError.svelte';

	const ws = getWorkspace();

	let open = $derived(ws.dialog?.kind === 'tag');
	const ids = $derived(ws.dialog?.kind === 'tag' ? ws.dialog.ids : []);

	/** Tags on the selected runs when the dialog opened, with how many of them have each. */
	const current = $derived.by(() => {
		const rows = ws.rowsOf(ids);
		return untrack(() => {
			const counts: Record<string, number> = {};
			for (const r of rows) {
				const tags = ws.cell(r, 'tags');
				if (Array.isArray(tags)) for (const t of tags) counts[t] = (counts[t] ?? 0) + 1;
			}
			return Object.entries(counts).toSorted(([a], [b]) => a.localeCompare(b));
		});
	});

	let removed = $derived.by(() => {
		void ws.dialog;
		return [] as string[];
	});
	const toggleRemoved = (tag: string) =>
		(removed = removed.includes(tag) ? removed.filter((t) => t !== tag) : [...removed, tag]);

	const runs = (n: number) => `${n.toLocaleString('en-US')} ${n === 1 ? 'run' : 'runs'}`;
	const nameOf = (id: string) => ws.page.runs.find((r) => r.id === id)?.name ?? id;

	const mutation = new Mutation<{ count: number }>({
		runs: true,
		validate: (form) =>
			parseTags(formText(form, 'add')).length || form.getAll('remove').length
				? null
				: 'Add or remove a tag',
		success: ({ count }) => `Updated tags on ${runs(count)}`,
		done: () => (open = false)
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
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>Tag {ids.length === 1 ? nameOf(ids[0]) : runs(ids.length)}</Dialog.Title>
		</Dialog.Header>
		<form method="POST" action="?/tag" class="grid gap-4" use:enhance={mutation.enhance}>
			{#each ids as id (id)}
				<input type="hidden" name="run" value={id} />
			{/each}
			{#each removed as tag (tag)}
				<input type="hidden" name="remove" value={tag} />
			{/each}
			<div class="grid gap-2">
				<Label for="tag-add">Add tags</Label>
				<Input id="tag-add" name="add" placeholder="baseline, lr-sweep" autocomplete="off" />
				<p class="text-xs text-muted-foreground">Separate tags with commas or spaces.</p>
			</div>
			{#if current.length}
				<div class="grid gap-2">
					<p class="text-sm font-medium">Current tags</p>
					<div class="flex flex-wrap gap-1.5">
						{#each current as [tag, count] (tag)}
							{@const off = removed.includes(tag)}
							<button
								type="button"
								class={[
									'inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs transition-colors',
									off
										? 'bg-destructive/10 text-destructive line-through'
										: 'bg-muted text-foreground hover:bg-[color-mix(in_srgb,var(--muted),var(--foreground)_6%)]'
								]}
								aria-pressed={off}
								title={off ? `Keep ${tag}` : `Remove ${tag}`}
								onclick={() => toggleRemoved(tag)}
							>
								{tag}
								{#if ids.length > 1}<span class="text-muted-foreground">{count}</span>{/if}
								<XIcon class="size-3 opacity-60" />
							</button>
						{/each}
					</div>
					<p class="text-xs text-muted-foreground">
						{removed.length
							? `Removing ${removed.length} ${removed.length === 1 ? 'tag' : 'tags'}.`
							: 'Click a tag to remove it.'}
					</p>
				</div>
			{/if}
			<FormError error={mutation.error} ids={mutation.ids} label={nameOf} />
			<Dialog.Footer>
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props} variant="outline">Cancel</Button>
					{/snippet}
				</Dialog.Close>
				<Button type="submit" disabled={mutation.pending}>
					{mutation.pending ? 'Saving…' : 'Save tags'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
