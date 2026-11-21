<script lang="ts">
	import { onMount } from 'svelte';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import type { Selection } from '#lib/selection.svelte.ts';

	interface Props {
		selection: Selection;
		/** The row's key, or none for the header's checkbox, which checks every row. */
		key?: string;
		/** The row's name, for the checkbox's label. */
		name?: string;
	}

	let { selection, key, name }: Props = $props();

	let mounted = $state(false);
	onMount(() => (mounted = true));

	// Hidden until its row (or the header, the table) is hovered or focused; all of them show
	// once anything is checked, and always on touch screens (D130).
	const hidden = $derived(
		selection.size
			? ''
			: key === undefined
				? 'opacity-0 group-focus-within/table:opacity-100 group-hover/table:opacity-100 pointer-coarse:opacity-100'
				: 'opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100 pointer-coarse:opacity-100'
	);
</script>

{#if key === undefined}
	<!--
		Never disabled on the server: a listing that knows its rows before it hydrates would keep
		the server’s `disabled` (hydration doesn't correct attributes), so it applies once mounted.
	-->
	<Checkbox
		class={hidden}
		checked={selection.all}
		indeterminate={selection.some}
		disabled={mounted && !selection.keys.length}
		onCheckedChange={selection.toggleAll}
		aria-label={selection.all ? 'Deselect all' : 'Select all'}
	/>
{:else}
	<Checkbox
		class={hidden}
		bind:checked={() => selection.has(key), () => {}}
		onclick={(e: MouseEvent) => selection.click(key, e.shiftKey)}
		aria-label="Select {name ?? key}"
	/>
{/if}
