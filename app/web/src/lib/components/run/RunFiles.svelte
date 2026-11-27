<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import FileIcon from '@lucide/svelte/icons/file';
	import { onMount } from 'svelte';
	import type { ResolvedPathname } from '$app/types';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import type { RunFile } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';
	import Skeleton from '#lib/components/Skeleton.svelte';
	import DataTable, { ROW_LINK, type Column } from '#lib/components/DataTable.svelte';

	interface Props {
		/** `/api/projects/…/runs/{run}/files` */
		url: string;
		now: number;
	}

	let { url, now }: Props = $props();

	let files = $state.raw<RunFile[] | null>(null);
	let failed = $state(false);

	onMount(() => {
		fetch(url)
			.then((res) => (res.ok ? res.json() : Promise.reject(new Error(res.statusText))))
			.then((body: { files: RunFile[] }) => {
				files = body.files.toSorted((a, b) => a.name.localeCompare(b.name));
			})
			.catch(() => (failed = true));
	});

	// A file of the dashboard's API, not a page.
	const fileUrl = (name: string) =>
		`${url}/${name.split('/').map(encodeURIComponent).join('/')}` as ResolvedPathname;

	const columns: Column[] = [
		{ id: 'name', label: 'Name' },
		{ id: 'size', label: 'Size', align: 'end' },
		{ id: 'updated', label: 'Updated', align: 'end', from: 'sm' }
	];
</script>

{#snippet cell(f: RunFile, id: string)}
	{#if id === 'name'}
		<!-- The link covers the row. Plain text and images open; anything else downloads. -->
		<a
			class={[ROW_LINK, 'block truncate font-mono text-[12.5px]']}
			href={fileUrl(f.name)}
			target="_blank"
			rel="noopener"
			data-sveltekit-reload
			{@attach hint(f.name)}>{f.name}</a
		>
	{:else if id === 'size'}
		<span class="text-muted-foreground">{bytes(f.size)}</span>
	{:else}
		<span class="text-muted-foreground" {@attach hint(dateTime(f.updated_at))}
			>{relativeTime(f.updated_at, now)}</span
		>
	{/if}
{/snippet}

{#snippet lead()}<FileIcon />{/snippet}

<!-- The list tables' look, as a model's files: full-bleed rows under a sticky header. -->
<div class="@container absolute inset-0 overflow-y-auto pb-16">
	{#if failed}
		<EmptyState tone="error">Couldn’t load the run’s files</EmptyState>
	{:else if !files}
		<Skeleton count={8} />
	{:else if !files.length}
		<EmptyState>No files uploaded</EmptyState>
	{:else}
		<DataTable {columns} rows={files} key={(f) => f.name} {cell} {lead} dense label="Files" />
	{/if}
</div>
