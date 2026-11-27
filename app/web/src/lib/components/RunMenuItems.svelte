<script lang="ts">
	import { goto } from '$app/navigation';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { menuRows, runActions } from '#lib/workspace/run-actions.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import TagsEditor from './TagsEditor.svelte';

	/** A row's menu items: its own actions, or its selection's. */
	let { row }: { row: number } = $props();

	const ws = getWorkspace();
	const rows = $derived(menuRows(ws, row));
	const actions = $derived(runActions(ws, rows, rows.length > 1));
	const ids = $derived(rows.map((r) => ws.page.runs[r].id));
</script>

{#if rows.length > 1}
	<DropdownMenu.Label class="text-xs font-normal text-muted-foreground">
		{rows.length} selected
	</DropdownMenu.Label>
{/if}
{#each actions as a (a.key)}
	{#if a.destructive}<DropdownMenu.Separator />{/if}
	{#if a.key === 'tag'}
		<!-- Tags open beside the menu, which dims while they're edited. -->
		<DropdownMenu.Sub>
			<DropdownMenu.SubTrigger><a.icon /> Tags</DropdownMenu.SubTrigger>
			<DropdownMenu.SubContent class="w-72 p-0" sideOffset={6}>
				<TagsEditor {ids} />
			</DropdownMenu.SubContent>
		</DropdownMenu.Sub>
	{:else}
		<DropdownMenu.Item
			variant={a.destructive ? 'destructive' : 'default'}
			onSelect={() => {
				// eslint-disable-next-line svelte/no-navigation-without-resolve -- built by compareHref
				if (a.href) void goto(a.href);
				else a.run?.();
			}}
		>
			<a.icon />
			{a.label}
		</DropdownMenu.Item>
	{/if}
{/each}
