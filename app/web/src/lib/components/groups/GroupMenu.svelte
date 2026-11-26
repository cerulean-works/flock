<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { editGroupHref } from '#lib/groups.ts';
	import DeleteGroupDialog from './DeleteGroupDialog.svelte';

	/** A group's menu in the sidebar, as a project's: edit it, or delete it. */

	interface Props {
		entity: string;
		project: string;
		group: { id: number; name: string };
		class?: string;
	}

	let { entity, project, group, class: className }: Props = $props();

	let deleting = $state(false);
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant="ghost"
				size="icon-xs"
				class={className}
				aria-label="{group.name} actions"
			>
				<EllipsisIcon />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-44">
		<DropdownMenu.Item>
			{#snippet child({ props })}
				<a {...props} href={editGroupHref(entity, project, group.id)}>
					<PencilIcon /> Edit group…
				</a>
			{/snippet}
		</DropdownMenu.Item>
		<DropdownMenu.Separator />
		<DropdownMenu.Item variant="destructive" onSelect={() => (deleting = true)}>
			<Trash2Icon /> Delete group…
		</DropdownMenu.Item>
	</DropdownMenu.Content>
</DropdownMenu.Root>

<DeleteGroupDialog bind:open={deleting} {entity} {project} {group} />
