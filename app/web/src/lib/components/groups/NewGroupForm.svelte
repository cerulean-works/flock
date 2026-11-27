<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto, invalidate } from '$app/navigation';
	import { Input } from '#lib/components/ui/input/index.js';
	import AnchoredForm from '#lib/components/AnchoredForm.svelte';
	import {
		GROUPS,
		GroupsError,
		groupHref,
		groupsApi,
		groupsCall,
		toFlockFilter,
		type Group
	} from '#lib/groups.ts';
	import { newGroup } from '#lib/workspace/new-group.svelte.ts';
	import type { Workspace } from '#lib/workspace/workspace.svelte.ts';

	/**
	 * **New group…** from a runs menu: the runs it was opened on, named here, made a group by hand
	 * (D216) as the editor's Manual mode makes one. The page stays; a toast opens the group.
	 */
	let { ws }: { ws: Workspace } = $props();

	let name = $state('');
	let pending = $state(false);
	let error = $state<string | null>(null);

	// Each opening starts from the name offered for its runs.
	$effect(() => {
		if (newGroup.open) {
			name = newGroup.name;
			error = null;
		}
	});

	async function create() {
		const trimmed = name.trim();
		if (!trimmed) {
			error = 'Name the group';
			return;
		}
		const { entity, project } = ws.meta;
		const ids = [...newGroup.ids];
		pending = true;
		error = null;
		try {
			const created = await groupsCall<Group>(groupsApi(entity, project), {
				method: 'POST',
				// No rule, exactly these runs, as a group made by hand in the editor.
				body: { name: trimmed, filter: toFlockFilter([]), runs: ids }
			});
			await invalidate(GROUPS);
			for (const id of ids) ws.selected.delete(id);
			newGroup.open = false;
			const href = groupHref(entity, project, created.id);
			toast.success(`Created ${created.name} · ${ids.length} run${ids.length === 1 ? '' : 's'}`, {
				action: { label: 'Open', onClick: () => void goto(href) }
			});
		} catch (e) {
			error = e instanceof GroupsError ? e.message : 'Couldn’t create the group';
		} finally {
			pending = false;
		}
	}
</script>

<AnchoredForm
	bind:open={newGroup.open}
	anchor={newGroup.anchor}
	title="New group"
	submit="Create · {newGroup.ids.length} run{newGroup.ids.length === 1 ? '' : 's'}"
	{pending}
	{error}
	align="start"
	onsubmit={create}
>
	<Input
		bind:value={name}
		oninput={() => (error = null)}
		aria-label="Group name"
		placeholder="Group name"
		autocomplete="off"
		spellcheck="false"
		aria-invalid={!!error}
	/>
</AnchoredForm>
