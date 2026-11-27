<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import type { Scope } from '#lib/scope.ts';
	import FileIcon from '@lucide/svelte/icons/file';
	import { folderHref } from '#lib/models.ts';
	import type { ModelVersionDetail } from '#lib/types.ts';
	import { bytes } from '#lib/utils.ts';

	let { scope, version }: { scope: Scope; version: ModelVersionDetail } = $props();
	import DataTable, { ROW_LINK, type Column } from '../DataTable.svelte';

	type File = ModelVersionDetail['files'][number];
	const columns: Column[] = [
		{ id: 'path', label: 'Path' },
		{ id: 'sha256', label: 'sha256', size: 'fit', from: 'lg' },
		{ id: 'size', label: 'Size', align: 'end' }
	];
</script>

{#snippet cell(file: File, id: string)}
	{#if id === 'path'}
		<a
			class={[ROW_LINK, 'block truncate']}
			href={folderHref(scope.org, `${version.prefix}${file.path}`)}
			{@attach hint(file.path)}>{file.path}</a
		>
	{:else if id === 'sha256'}
		<span class="font-mono text-[12px] text-muted-foreground" {@attach hint(file.sha256)}
			>{file.sha256.slice(0, 12)}</span
		>
	{:else}
		<span class="text-muted-foreground">{bytes(file.size)}</span>
	{/if}
{/snippet}

{#snippet lead()}<FileIcon />{/snippet}

<!-- The list tables' look (Gate 2): each row opens the file in Storage. -->
<DataTable {columns} rows={version.files} key={(f) => f.path} {cell} {lead} dense label="Files" />
