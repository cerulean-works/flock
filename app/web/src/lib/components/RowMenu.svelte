<script lang="ts" module>
	/** A row menu's ⋯ trigger: shown on row hover, focus or while open; always on touch. */
	export const ROW_MENU_TRIGGER =
		'grid size-6 place-items-center rounded-md text-muted-foreground opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 hover:bg-[color-mix(in_srgb,var(--muted),var(--foreground)_6%)] hover:text-foreground focus-visible:opacity-100 data-[state=open]:opacity-100 pointer-coarse:opacity-100';
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';

	interface Props {
		/** The row's name, for the button's label. */
		name: string;
		/** The menu's items (`RowMenuItems`). */
		children: Snippet;
	}

	let { name, children }: Props = $props();
</script>

<!-- A list row's ⋯ menu (D133): shown on row hover, always on touch screens, and while open. -->
<DropdownMenu.Root>
	<DropdownMenu.Trigger class={ROW_MENU_TRIGGER} aria-label="Actions for {name}">
		<EllipsisIcon class="size-4" />
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-48">
		{@render children()}
	</DropdownMenu.Content>
</DropdownMenu.Root>
