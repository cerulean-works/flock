<script lang="ts">
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { toast } from 'svelte-sonner';
	import { goto, invalidate } from '$app/navigation';
	import { page } from '$app/state';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import {
		GROUPS,
		GroupsError,
		groupsApi,
		groupsCall,
		type Group,
		type GroupList
	} from '#lib/groups.ts';
	import { openNewGroup } from '#lib/workspace/new-group.svelte.ts';
	import { menuRows, runActions } from '#lib/workspace/run-actions.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import { openMenuTrigger } from './AnchoredForm.svelte';
	import MenuSearch from './MenuSearch.svelte';
	import TagsEditor from './TagsEditor.svelte';

	/** A row's menu items: its own actions, or its selection's. */
	let { row }: { row: number } = $props();

	const ws = getWorkspace();
	const rows = $derived(menuRows(ws, row));
	const ids = $derived(rows.map((r) => ws.page.runs[r].id));
	// The project's groups, where the project has them (the sidebar reads the same list).
	const groups = $derived((page.data.groups as GroupList | null | undefined)?.groups ?? null);
	const actions = $derived(
		runActions(ws, rows, rows.length > 1).filter((a) => a.key !== 'group' || groups)
	);

	let query = $state('');
	const matches = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return (groups ?? []).filter((g) => !q || g.name.toLowerCase().includes(q));
	});
	const runsText = (n: number) => (n === 1 ? ws.nameOf(rows[0]) : `${n} runs`);

	async function addTo(group: Group) {
		const n = ids.length;
		try {
			await groupsCall(groupsApi(ws.meta.entity, ws.meta.project, String(group.id)), {
				method: 'PATCH',
				body: { add: ids }
			});
			await invalidate(GROUPS);
			toast.success(`Added ${runsText(n)} to ${group.name}`);
		} catch (e) {
			toast.error(e instanceof GroupsError ? e.message : 'Couldn’t add to the group');
		}
	}
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
	{:else if a.key === 'group'}
		<!-- The project's groups, searchable; New group… at the foot names one for these runs. -->
		<DropdownMenu.Sub onOpenChange={(o) => o && (query = '')}>
			<DropdownMenu.SubTrigger><a.icon /> {a.label}</DropdownMenu.SubTrigger>
			<DropdownMenu.SubContent class="w-60 p-0" sideOffset={6}>
				{#if groups?.length}
					<MenuSearch
						placeholder="Search groups…"
						label="Search groups"
						bind:value={query}
						onkeydown={(e) => {
							if (e.key !== 'Escape' && e.key !== 'Tab') e.stopPropagation();
						}}
					/>
					<div class="max-h-64 overflow-y-auto p-1">
						{#each matches as g (g.id)}
							<DropdownMenu.Item onSelect={() => void addTo(g)}>
								<span
									class="size-2.5 flex-none rounded-[3px]"
									style:background-color={g.color}
									aria-hidden="true"
								></span>
								<span class="truncate">{g.name}</span>
							</DropdownMenu.Item>
						{:else}
							{#if query}
								<p class="px-2 py-1.5 text-xs text-muted-foreground">No groups match</p>
							{/if}
						{/each}
					</div>
					<DropdownMenu.Separator class="my-0" />
				{/if}
				<div class="p-1">
					<DropdownMenu.Item onSelect={() => openNewGroup(ws, rows, openMenuTrigger())}>
						<PlusIcon /> New group…
					</DropdownMenu.Item>
				</div>
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
