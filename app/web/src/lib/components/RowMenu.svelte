<script lang="ts">
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';

	interface Props {
		/** The row's name, for the button's label. */
		name: string;
		onrename: () => void;
		ondelete: () => void;
	}

	let { name, onrename, ondelete }: Props = $props();
</script>

<!-- A list row's ⋯ menu (D133): shown on row hover, always on touch screens, and while open. -->
<DropdownMenu.Root>
	<DropdownMenu.Trigger
		class="grid size-6 place-items-center rounded-md text-muted-foreground opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 hover:bg-[color-mix(in_srgb,var(--muted),var(--foreground)_6%)] hover:text-foreground focus-visible:opacity-100 data-[state=open]:opacity-100 pointer-coarse:opacity-100"
		aria-label="Actions for {name}"
	>
		<EllipsisIcon class="size-4" />
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-40">
		<DropdownMenu.Item onSelect={onrename}>
			<PencilIcon /> Rename…
		</DropdownMenu.Item>
		<DropdownMenu.Separator />
		<DropdownMenu.Item variant="destructive" onSelect={ondelete}>
			<Trash2Icon /> Delete…
		</DropdownMenu.Item>
	</DropdownMenu.Content>
</DropdownMenu.Root>
