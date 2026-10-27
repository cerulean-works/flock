<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import type { SchemaField } from '#lib/types.ts';
	import Self from './SchemaField.svelte';

	let { field, depth = 0 }: { field: SchemaField; depth?: number } = $props();

	let open = $state(false);
	const meta = $derived(Object.entries(field.metadata));
</script>

<!-- A row of the Schema tab's tree: structs and lists open onto their children. -->
<li>
	<div
		class="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_5rem] items-baseline gap-3 border-b border-divider py-1.5 text-[13px]"
		style:padding-left="{depth * 1.25}rem"
	>
		<span class="flex min-w-0 items-center gap-1">
			{#if field.children.length}
				<button
					class="grid size-5 flex-none place-items-center rounded-sm text-muted-foreground hover:bg-muted"
					aria-expanded={open}
					aria-label="{open ? 'Hide' : 'Show'} the fields of {field.name}"
					onclick={() => (open = !open)}
				>
					<ChevronRightIcon class={['size-3.5 transition-transform', open && 'rotate-90']} />
				</button>
			{:else}
				<span class="size-5 flex-none"></span>
			{/if}
			<span class="truncate font-mono text-[12.5px]" title={field.name}>{field.name}</span>
		</span>
		<span class="min-w-0 font-mono text-[12px] break-all text-muted-foreground">{field.type}</span>
		<span class="text-muted-foreground">{field.nullable ? 'nullable' : 'required'}</span>
	</div>
	{#if meta.length}
		<dl
			class="grid grid-cols-[auto_1fr] gap-x-3 border-b border-divider py-1 text-[12px]"
			style:padding-left="{depth * 1.25 + 1.5}rem"
		>
			{#each meta as [key, value] (key)}
				<dt class="font-mono text-muted-foreground">{key}</dt>
				<dd class="min-w-0 truncate font-mono" title={value}>{value}</dd>
			{/each}
		</dl>
	{/if}
	{#if open}
		<ul>
			{#each field.children as child (child.name)}
				<Self field={child} depth={depth + 1} />
			{/each}
		</ul>
	{/if}
</li>
