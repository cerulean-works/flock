<script lang="ts">
	import type { DatasetInfo } from '#lib/types.ts';
	import SchemaField from './SchemaField.svelte';

	let { info }: { info: DatasetInfo } = $props();

	/** Schema metadata values longer than this are cut, and open on a click. */
	const CUT = 200;
	const metadata = $derived(Object.entries(info.metadata));
	const nested = $derived(info.schema.some((f) => f.children.length > 0));
</script>

<!-- The columns as a tree, the dataset's details and its schema metadata (D91). -->
<div class="grid gap-8">
	<section aria-labelledby="schema-columns">
		<h2 id="schema-columns" class="text-lg tracking-[-0.01em]">Columns</h2>
		<div
			class="mt-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_5rem] gap-3 border-b pb-1.5 text-xs text-muted-foreground"
		>
			<span class={[nested && 'pl-6']}>Name</span>
			<span>Type</span>
			<span>Nulls</span>
		</div>
		<ul>
			{#each info.schema as field (field.name)}
				<SchemaField {field} gutter={nested} />
			{/each}
		</ul>
	</section>

	<section aria-labelledby="schema-details">
		<h2 id="schema-details" class="text-lg tracking-[-0.01em]">Details</h2>
		<dl class="mt-2 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-[13px]">
			<dt class="text-muted-foreground">Data storage version</dt>
			<dd class="font-mono text-[12.5px]">{info.details.data_storage_version}</dd>
			<dt class="text-muted-foreground">Stable row IDs</dt>
			<dd>{info.details.stable_row_ids ? 'On' : 'Off'}</dd>
			<dt class="text-muted-foreground">Fragments</dt>
			<dd class="tabular-nums">{info.details.fragments.toLocaleString('en-US')}</dd>
		</dl>
		<h3 class="mt-4 text-[13px] text-muted-foreground">Indices</h3>
		{#if info.details.indices.length}
			<table class="mt-1 w-full border-collapse text-[13px]">
				<thead>
					<tr class="h-8 border-b text-left text-xs text-muted-foreground">
						<th class="px-2 font-medium">Name</th>
						<th class="px-2 font-medium">Type</th>
						<th class="px-2 font-medium">Columns</th>
					</tr>
				</thead>
				<tbody>
					{#each info.details.indices as index (index.name)}
						<tr class="h-8 border-b border-divider">
							<td class="px-2 font-mono text-[12.5px]">{index.name}</td>
							<td class="px-2">{index.type}</td>
							<td class="px-2 font-mono text-[12.5px]">{index.columns.join(', ')}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			<p class="mt-1 text-[13px] text-muted-foreground">This version has no indices.</p>
		{/if}
	</section>

	<section aria-labelledby="schema-metadata">
		<h2 id="schema-metadata" class="text-lg tracking-[-0.01em]">Schema metadata</h2>
		{#if metadata.length}
			<dl class="mt-2 grid gap-2 text-[13px]">
				{#each metadata as [key, value] (key)}
					<div class="grid gap-0.5">
						<dt class="font-mono text-[12.5px] text-muted-foreground">{key}</dt>
						<dd class="min-w-0 font-mono text-[12px] break-all">
							{#if value.length > CUT}
								<details>
									<summary class="cursor-pointer">
										{value.slice(0, CUT)}… <span class="text-link">Show all</span>
									</summary>
									<pre class="mt-1 whitespace-pre-wrap">{value}</pre>
								</details>
							{:else}
								{value}
							{/if}
						</dd>
					</div>
				{/each}
			</dl>
		{:else}
			<p class="mt-2 text-[13px] text-muted-foreground">This schema has no metadata.</p>
		{/if}
	</section>
</div>
