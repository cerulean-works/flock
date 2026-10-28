<script lang="ts">
	import Columns3Icon from '@lucide/svelte/icons/columns-3';
	import type { SvelteSet } from 'svelte/reactivity';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';

	interface Props {
		/** Every column of the dataset. */
		names: string[];
		/** The columns the viewer hid; they aren't read. */
		hidden: SvelteSet<string>;
	}

	let { names, hidden }: Props = $props();

	const shown = $derived(names.filter((n) => !hidden.has(n)).length);
</script>

<Popover.Root>
	<Popover.Trigger>
		{#snippet child({ props })}
			<Button {...props} variant="outline" size="sm">
				<Columns3Icon />
				Columns
				{#if hidden.size}<span class="text-muted-foreground">{shown} of {names.length}</span>{/if}
			</Button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content align="end" class="max-h-96 w-64 gap-1 overflow-y-auto">
		<div class="flex items-center justify-between pb-1">
			<p class="text-xs text-muted-foreground">Columns shown</p>
			{#if hidden.size}
				<button class="text-xs text-link hover:underline" onclick={() => hidden.clear()}>
					Show all
				</button>
			{/if}
		</div>
		{#each names as name (name)}
			<label class="flex items-center gap-2 py-1 text-[13px]">
				<Checkbox
					checked={!hidden.has(name)}
					onCheckedChange={(on) => (on ? hidden.delete(name) : hidden.add(name))}
				/>
				<span class="truncate font-mono text-[12.5px]">{name}</span>
			</label>
		{/each}
	</Popover.Content>
</Popover.Root>
