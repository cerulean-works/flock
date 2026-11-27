<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import { hint } from '#lib/hint.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';

	// The floating pager's "100 per page" control, after a divider: the runs table's and Home's.
	interface Props {
		size: number;
		sizes: readonly number[];
		/** What's counted, as "Runs per page". */
		label: string;
		onsize: (size: number) => void;
	}

	let { size, sizes, label, onsize }: Props = $props();

	let open = $state(false);

	function pick(value: string) {
		// Closed here: a navigation re-renders the menu, which would otherwise stay open.
		open = false;
		const n = Number(value);
		if (n !== size) onsize(n);
	}
</script>

<span class="mx-0.5 h-5 w-px bg-border" aria-hidden="true"></span>
<!-- A menu rather than a select: its items act on click, so the click doesn't land on the row
     under the menu once it closes. -->
<DropdownMenu.Root bind:open>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant="ghost"
				size="sm"
				class="gap-1 px-2 font-normal"
				aria-label={label}
				{@attach hint(label)}
			>
				{size} per page
				<ChevronDownIcon class="text-muted-foreground" />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content side="top" align="end" class="w-40">
		<DropdownMenu.Group>
			<DropdownMenu.Label>{label}</DropdownMenu.Label>
			<DropdownMenu.RadioGroup value={String(size)} onValueChange={pick}>
				{#each sizes as n (n)}
					<DropdownMenu.RadioItem value={String(n)}>{n}</DropdownMenu.RadioItem>
				{/each}
			</DropdownMenu.RadioGroup>
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>
