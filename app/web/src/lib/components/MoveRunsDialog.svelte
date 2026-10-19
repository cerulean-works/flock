<script lang="ts">
	import { enhance } from '$app/forms';
	import type { ResolvedPathname } from '$app/types';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { LIVE_STATES, type ProjectInfo } from '#lib/types.ts';
	import { formText, projectNameError } from '#lib/utils.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import FormError from './FormError.svelte';

	interface Props {
		/** Where to go once the runs are moved to project `to`, rather than reload this page. */
		leave?: (to: string) => ResolvedPathname;
	}

	let { leave }: Props = $props();

	const ws = getWorkspace();

	let open = $derived(ws.dialog?.kind === 'move');
	const ids = $derived(ws.dialog?.kind === 'move' ? ws.dialog.ids : []);
	const live = $derived(ws.rowsOf(ids).filter((r) => LIVE_STATES.has(ws.stateOf(r))).length);

	/** The Select value that stands for a project that doesn't exist yet. */
	const NEW = '\u0000new';
	let target = $derived.by(() => {
		void ws.dialog;
		return '';
	});
	let newName = $derived.by(() => {
		void ws.dialog;
		return '';
	});
	const creating = $derived(target === NEW);
	const to = $derived(creating ? newName.trim() : target);

	// The entity's other projects, fetched when the dialog opens.
	let projects = $state.raw<ProjectInfo[] | null>(null);
	let listError = $state<string | null>(null);
	$effect(() => {
		if (!open) return;
		const { entity, project } = ws.meta;
		const controller = new AbortController();
		listError = null;
		fetch(`/api/projects?${new URLSearchParams({ entity })}`, { signal: controller.signal })
			.then((res) => (res.ok ? res.json() : Promise.reject(new Error(res.statusText))))
			.then(({ projects: all }: { projects: ProjectInfo[] }) => {
				projects = all
					.filter((p) => p.name !== project)
					.toSorted((a, b) => b.updated_at - a.updated_at);
			})
			.catch((err: Error) => {
				if (err.name !== 'AbortError') listError = 'Couldn’t load projects';
			});
		return () => controller.abort();
	});

	const runs = (n: number) => `${n.toLocaleString('en-US')} ${n === 1 ? 'run' : 'runs'}`;
	const nameOf = (id: string) => ws.page.runs.find((r) => r.id === id)?.name ?? id;

	const mutation = new Mutation<{ count: number; to: string }>({
		runs: true,
		validate: (form) =>
			formText(form, 'to') ? projectNameError(formText(form, 'to')) : 'Choose a project',
		submit: () => ids.forEach((id) => ws.removing.add(id)),
		leave: ({ to }) => leave?.(to),
		success: ({ count, to }) => `Moved ${runs(count)} to ${to}`,
		done: () => {
			for (const id of ids) ws.selected.delete(id);
			open = false;
		}
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
			<Dialog.Title>
				Move {ids.length === 1 ? nameOf(ids[0]) : runs(ids.length)}
			</Dialog.Title>
			<Dialog.Description>
				To another project in {ws.meta.entity}.
			</Dialog.Description>
		</Dialog.Header>
		<form method="POST" action="?/move" class="grid gap-4" use:enhance={mutation.enhance}>
			{#each ids as id (id)}
				<input type="hidden" name="run" value={id} />
			{/each}
			<input type="hidden" name="to" value={to} />
			{#if creating}<input type="hidden" name="create" value="on" />{/if}
			<div class="grid gap-2">
				<Label for="move-target">Project</Label>
				<Select.Root type="single" value={target} onValueChange={(v) => (target = v)}>
					<Select.Trigger id="move-target" class="w-full">
						{#if creating}
							New project
						{:else if target}
							{target}
						{:else}
							<span class="text-muted-foreground">
								{projects || listError ? 'Choose a project' : 'Loading projects…'}
							</span>
						{/if}
					</Select.Trigger>
					<Select.Content>
						{#each projects ?? [] as p (p.name)}
							<Select.Item value={p.name} label={p.name}>
								{p.name}
								<span class="ml-auto text-xs text-muted-foreground tabular-nums">
									{runs(p.run_count)}
								</span>
							</Select.Item>
						{/each}
						{#if projects?.length}<Select.Separator />{/if}
						<Select.Item value={NEW} label="New project">New project…</Select.Item>
					</Select.Content>
				</Select.Root>
			</div>
			{#if creating}
				<div class="grid gap-2">
					<Label for="move-new-name">New project name</Label>
					<Input id="move-new-name" bind:value={newName} autocomplete="off" spellcheck="false" />
				</div>
			{/if}
			{#if live}
				<p class="text-[13px] text-muted-foreground">
					{live === ids.length && ids.length === 1
						? 'This run is still running.'
						: `${runs(live)} of these ${live === 1 ? 'is' : 'are'} still running.`}
					Runs can move once they finish.
				</p>
			{/if}
			<FormError error={mutation.error ?? listError} ids={mutation.ids} label={nameOf} />
			<Dialog.Footer>
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props} variant="outline">Cancel</Button>
					{/snippet}
				</Dialog.Close>
				<Button type="submit" disabled={!to || live > 0 || mutation.pending}>
					{mutation.pending
						? 'Moving…'
						: ids.length === 1
							? 'Move run'
							: `Move ${runs(ids.length)}`}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
