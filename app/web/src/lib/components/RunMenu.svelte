<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import TagIcon from '@lucide/svelte/icons/tag';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	let { row }: { row: number } = $props();

	const ws = getWorkspace();
</script>

<!-- Shown on row hover; stays visible while its menu is open. -->
<DropdownMenu.Root>
	<DropdownMenu.Trigger
		class="run-menu -mr-1.5 grid size-6 flex-none place-items-center rounded-md text-muted-foreground opacity-0 group-hover/row:opacity-100 hover:bg-[color-mix(in_srgb,var(--muted),var(--foreground)_6%)] hover:text-foreground focus-visible:opacity-100 data-[state=open]:opacity-100"
		aria-label="Actions for {ws.nameOf(row)}"
	>
		<EllipsisIcon class="size-4" />
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="start" class="w-44">
		<DropdownMenu.Item onSelect={() => ws.openDialog('edit', row)}>
			<PencilIcon /> Edit run…
		</DropdownMenu.Item>
		<DropdownMenu.Item onSelect={() => ws.openDialog('tag', row)}>
			<TagIcon /> Tags…
		</DropdownMenu.Item>
		<DropdownMenu.Item onSelect={() => ws.openDialog('move', row)}>
			<FolderInputIcon /> Move to project…
		</DropdownMenu.Item>
		<DropdownMenu.Separator />
		<DropdownMenu.Item variant="destructive" onSelect={() => ws.openDialog('delete', row)}>
			<Trash2Icon /> Delete run…
		</DropdownMenu.Item>
	</DropdownMenu.Content>
</DropdownMenu.Root>
