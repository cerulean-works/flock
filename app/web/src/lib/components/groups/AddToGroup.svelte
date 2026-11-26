<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { toast } from 'svelte-sonner';
	import { invalidate } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import {
		GROUPS,
		GroupsError,
		groupsApi,
		groupsCall,
		newGroupHref,
		type Group
	} from '#lib/groups.ts';

	/**
	 * **Add to group** for an ungrouped run (D220): every group, searchable, with checkboxes, and
	 * **New group with this run…**. Nothing is pre-checked or recommended. The run is added by
	 * hand, so it stays whatever the groups' filters say (D216).
	 */

	interface Props {
		entity: string;
		project: string;
		run: { id: string; name: string };
		groups: Group[];
	}

	let { entity, project, run, groups }: Props = $props();

	let open = $state(false);
	let query = $state('');
	let chosen = $state<number[]>([]);
	let pending = $state(false);

	const matches = $derived(
		query.trim()
			? groups.filter((g) => g.name.toLowerCase().includes(query.trim().toLowerCase()))
			: groups
	);

	function onOpenChange(o: boolean) {
		if (!o) return;
		query = '';
		chosen = [];
	}

	function toggle(id: number, on: boolean) {
		chosen = on ? [...chosen, id] : chosen.filter((c) => c !== id);
	}

	async function add() {
		pending = true;
		const names = groups.filter((g) => chosen.includes(g.id)).map((g) => g.name);
		const failed: string[] = [];
		await Promise.all(
			chosen.map((id) =>
				groupsCall(groupsApi(entity, project, String(id)), {
					method: 'PATCH',
					body: { add: [run.id] }
				}).catch((err: unknown) => {
					failed.push(err instanceof GroupsError ? err.message : 'Something went wrong');
				})
			)
		);
		await invalidate(GROUPS);
		pending = false;
		if (failed.length) {
			toast.error(failed[0]);
			return;
		}
		open = false;
		toast.success(
			names.length === 1
				? `Added ${run.name} to ${names[0]}`
				: `Added ${run.name} to ${names.length} groups`
		);
	}
</script>

<Popover.Root bind:open {onOpenChange}>
	<Popover.Trigger>
		{#snippet child({ props })}
			<Button {...props} variant="outline" size="xs" class="flex-none">
				Add to group <ChevronDownIcon class="text-muted-foreground" />
			</Button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content align="start" class="w-72 gap-1 p-1.5" aria-label="Add {run.name} to groups">
		<div class="grid gap-0.5 px-2 pt-1.5 pb-1">
			<span class="truncate font-medium">Add {run.name} to</span>
			<span class="text-xs text-muted-foreground">Pick any number of groups.</span>
		</div>
		{#if groups.length > 5}
			<label
				class="mx-0.5 my-1 flex h-7 items-center gap-2 rounded-md border border-input px-2 focus-within:border-ring"
			>
				<SearchIcon class="size-3.5 flex-none text-muted-foreground" />
				<input
					type="search"
					class="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-faint"
					placeholder="Find a group"
					aria-label="Find a group"
					bind:value={query}
				/>
			</label>
		{/if}
		<div class="grid max-h-64 gap-px overflow-y-auto">
			{#each matches as g (g.id)}
				{@const checked = chosen.includes(g.id)}
				<label
					class={[
						'flex h-8 cursor-pointer items-center gap-2.5 rounded-md px-2 hover:bg-muted',
						checked && 'bg-selection/40'
					]}
				>
					<Checkbox {checked} onCheckedChange={(on) => toggle(g.id, on)} />
					<span class="size-2 flex-none rounded-[2px]" style:background={g.color}></span>
					<span class="min-w-0 flex-1 truncate">{g.name}</span>
					<span class="text-xs text-muted-foreground tabular-nums">{g.run_count}</span>
				</label>
			{:else}
				<p class="px-2 py-1.5 text-xs text-muted-foreground">
					{groups.length ? 'No matching groups' : 'No groups yet'}
				</p>
			{/each}
		</div>
		<a
			class="flex h-8 items-center gap-2.5 rounded-md px-2 text-foreground hover:bg-muted [&_svg]:size-4 [&_svg]:text-muted-foreground"
			href={newGroupHref(entity, project, { runs: [run.id] })}
		>
			<PlusIcon /> New group with this run…
		</a>
		<div class="mt-0.5 flex items-center gap-2 border-t border-divider px-1 pt-2 pb-1">
			<span class="flex-1 text-xs leading-snug text-muted-foreground">
				Added by hand, so it stays even if it doesn’t match a group’s filters.
			</span>
			<Button size="sm" disabled={!chosen.length || pending} onclick={add}>
				Add to {chosen.length || ''}
			</Button>
		</div>
	</Popover.Content>
</Popover.Root>
