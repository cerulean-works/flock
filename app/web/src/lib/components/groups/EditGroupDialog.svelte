<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import type { GroupDetail, GroupList, RunsResult } from '#lib/groups.ts';
	import type { Owner } from '#lib/types.ts';
	import GroupEditor from './GroupEditor.svelte';
	import { loadEditor } from './editor.ts';

	/**
	 * **Edit group** (D190): the editor in a dialog over the group's page. The project's runs
	 * are read each time it opens, and each opening starts a fresh draft. Saving patches the
	 * group, invalidates `GROUPS` (which reads the group's page and the sidebar again) and
	 * closes; **Cancel** and Escape close without saving.
	 */

	interface Props {
		open?: boolean;
		group: GroupDetail;
		/** Opened from **N new matches**: they come first (D183). */
		fresh?: boolean;
	}

	let { open = $bindable(false), group, fresh = false }: Props = $props();

	const entity = $derived(page.params.entity!);
	const project = $derived(page.params.project!);

	let editor = $state.raw<{ runs: RunsResult; summaryKeys: string[] } | null>(null);
	let failed = $state(false);
	/** Bumped on each opening, so the editor's draft starts over. */
	let opening = $state(0);

	$effect(() => {
		if (!open) return;
		const [e, p] = [entity, project];
		const n = untrack(() => ++opening);
		editor = null;
		failed = false;
		loadEditor(fetch, e, p)
			.then((loaded) => {
				if (n === opening) editor = loaded;
			})
			.catch(() => {
				if (n === opening) failed = true;
			});
	});
</script>

<Dialog.Root bind:open>
	<Dialog.Content
		showCloseButton={false}
		class="flex h-[calc(100dvh-3rem)] w-[calc(100vw-3rem)] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none"
	>
		<Dialog.Title class="sr-only">Edit {group.name}</Dialog.Title>
		<Dialog.Description class="sr-only">
			Tick runs in or out of the group, set its filters, or ask the assistant.
		</Dialog.Description>
		{#if editor}
			{#key opening}
				<GroupEditor
					{entity}
					{project}
					runs={editor.runs}
					summaryKeys={editor.summaryKeys}
					people={(page.data.people ?? []) as Owner[]}
					groupList={(page.data.groups ?? null) as GroupList | null}
					{group}
					{fresh}
					oncancel={() => (open = false)}
					onsaved={() => (open = false)}
				/>
			{/key}
		{:else}
			<div class="grid flex-1 place-items-center text-sm text-muted-foreground">
				{#if failed}
					<p>
						Couldn’t read the project’s runs.
						<button type="button" class="text-link hover:underline" onclick={() => (open = false)}>
							Close
						</button>
					</p>
				{:else}
					<span class="flex items-center gap-2">
						<LoaderCircleIcon class="size-4 animate-spin" /> Loading runs…
					</span>
				{/if}
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
