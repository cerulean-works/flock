<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import RunMenuItems from './RunMenuItems.svelte';

	let { row }: { row: number } = $props();

	const ws = getWorkspace();
</script>

<!-- Shown on row hover; stays visible while its menu is open. In a selection of several, it acts
     on the selection (two eval runs compare from here). -->
<DropdownMenu.Root>
	<DropdownMenu.Trigger
		class="run-menu -mr-1.5 grid size-6 flex-none place-items-center rounded-md text-muted-foreground opacity-0 group-hover/row:opacity-100 hover:bg-[color-mix(in_srgb,var(--muted),var(--foreground)_6%)] hover:text-foreground focus-visible:opacity-100 data-[state=open]:opacity-100 pointer-coarse:opacity-100"
		aria-label="Actions for {ws.nameOf(row)}"
	>
		<EllipsisIcon class="size-4" />
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="start" class="w-48">
		<RunMenuItems {row} />
	</DropdownMenu.Content>
</DropdownMenu.Root>
