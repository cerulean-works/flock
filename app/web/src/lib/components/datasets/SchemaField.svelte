<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import type { SchemaField } from '#lib/types.ts';
	import Self from './SchemaField.svelte';

	/** `gutter`: whether any column opens, so names keep room for the chevron to line up. */
	let {
		field,
		depth = 0,
		gutter = true
	}: { field: SchemaField; depth?: number; gutter?: boolean } = $props();

	let open = $state(false);
	const meta = $derived(Object.entries(field.metadata).sort(([a], [b]) => a.localeCompare(b)));
</script>

<!-- A row of the Schema tab's tree: structs and lists open onto their children. -->
<li class={['border-b border-divider', depth > 0 && 'last:border-b-0']}>
	<div
		class="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_5rem] items-start gap-3 py-2 text-[13px]"
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
			{:else if gutter}
				<span class="size-5 flex-none"></span>
			{/if}
			<span class="truncate font-mono text-[12.5px]" title={field.name}>{field.name}</span>
		</span>
		<!-- The field's own metadata sits under its type, as part of the same row. -->
		<div class="min-w-0 py-0.5">
			<span class="font-mono text-[12px] break-all text-muted-foreground">{field.type}</span>
			{#if meta.length}
				<dl class="mt-1 flex flex-wrap gap-1" aria-label="Metadata of {field.name}">
					{#each meta as [key, value] (key)}
						<div
							class="inline-flex max-w-full min-w-0 items-baseline gap-1.5 rounded-sm bg-card px-1.5 py-0.5 font-mono text-[11px]"
						>
							<dt class="text-faint">{key}</dt>
							<dd class="min-w-0 truncate" title={value}>{value}</dd>
						</div>
					{/each}
				</dl>
			{/if}
		</div>
		<span class="py-0.5 text-muted-foreground">{field.nullable ? 'nullable' : 'required'}</span>
	</div>
	{#if open}
		<ul class="border-t border-divider">
			{#each field.children as child (child.name)}
				<Self field={child} depth={depth + 1} />
			{/each}
		</ul>
	{/if}
</li>
