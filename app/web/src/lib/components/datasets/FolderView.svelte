<script lang="ts">
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FileIcon from '@lucide/svelte/icons/file';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import { resolve } from '$app/paths';
	import { Button } from '#lib/components/ui/button/index.js';
	import {
		DATASETS_BUCKET,
		datasetCall,
		datasetsApi,
		datasetsHref,
		fileHref,
		writeSnippet
	} from '#lib/datasets.ts';
	import type { DatasetFolder } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';

	interface Props {
		org: string;
		/** The folder shown: empty for the bucket's root. */
		path: string;
		/** Its first page, from the server. */
		folder: DatasetFolder;
		now: number;
	}

	let { org, path, folder, now }: Props = $props();

	// The server's first page, then pages loaded here with "Load more". Datasets come first in
	// each page, then folders, then files (D87).
	let datasets = $derived(folder.datasets);
	let folders = $derived(folder.folders);
	let files = $derived(folder.files);
	let cursor = $derived(folder.cursor);
	let loading = $state(false);
	let failed = $state<string | null>(null);
	const empty = $derived(!datasets.length && !folders.length && !files.length);

	async function more() {
		if (!cursor) return;
		loading = true;
		failed = null;
		try {
			const page = await datasetCall<DatasetFolder>(datasetsApi(org, 'at', { path, cursor }));
			datasets = [...datasets, ...page.datasets];
			folders = [...folders, ...page.folders];
			files = [...files, ...page.files];
			cursor = page.cursor;
		} catch (e) {
			failed = (e as Error).message;
		} finally {
			loading = false;
		}
	}
</script>

<div class="grid gap-3">
	{#if empty && !path}
		<div class="max-w-[68ch]">
			<p class="text-[15px]">No datasets yet.</p>
			<p class="mt-1 text-muted-foreground">
				Write a Lance dataset into {DATASETS_BUCKET} and it shows up here.
			</p>
			<div class="mt-3"><CopyBlock code={writeSnippet(org)} /></div>
			<p class="mt-3 text-[13px] text-muted-foreground">
				For tools that can’t run <code class="font-mono text-[0.9em]">flock</code>, create a
				long-lived key on your
				<a
					class="text-link underline-offset-3 hover:underline"
					href="{resolve('/(app)/account')}#s3-keys">account page</a
				>.
			</p>
		</div>
	{:else if empty}
		<p class="text-muted-foreground">This folder is empty.</p>
	{:else}
		<table class="w-full table-fixed border-collapse text-[13.5px]">
			<thead>
				<tr class="h-10 border-b text-left text-xs font-medium text-muted-foreground">
					<th class="px-2 font-medium">Name</th>
					<th class="hidden w-36 px-2 font-medium sm:table-cell">Version</th>
					<th class="hidden w-36 px-2 font-medium sm:table-cell">Size</th>
					<th class="w-36 px-2 font-medium">Updated</th>
				</tr>
			</thead>
			<tbody>
				{#each datasets as d (`d:${d.path}`)}
					<tr class="h-10 border-b border-divider hover:bg-muted">
						<td class="truncate px-2">
							<a
								class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
								href={datasetsHref(org, d.path)}
							>
								<DatabaseIcon class="size-4 flex-none text-muted-foreground" />
								<span class="truncate">{d.name}</span>
							</a>
						</td>
						<td class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell">
							{d.version === null ? 'Unknown' : `v${d.version}`}
						</td>
						<td class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell">
							{d.size == null ? '—' : bytes(d.size)}
						</td>
						<td
							class="px-2 text-muted-foreground tabular-nums"
							title={d.updated_at ? dateTime(d.updated_at) : undefined}
						>
							{d.updated_at ? relativeTime(d.updated_at, now) : '—'}
						</td>
					</tr>
				{/each}
				{#each folders as f (`f:${f.path}`)}
					<tr class="h-10 border-b border-divider hover:bg-muted">
						<td class="truncate px-2">
							<a
								class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
								href={datasetsHref(org, f.path)}
							>
								<FolderIcon class="size-4 flex-none text-muted-foreground" />
								<span class="truncate">{f.name}/</span>
							</a>
						</td>
						<td class="hidden px-2 text-muted-foreground sm:table-cell">—</td>
						<td class="hidden px-2 text-muted-foreground sm:table-cell">—</td>
						<td class="px-2 text-muted-foreground">—</td>
					</tr>
				{/each}
				{#each files as file (`o:${file.key}`)}
					<tr class="h-10 border-b border-divider hover:bg-muted">
						<td class="truncate px-2">
							<a
								class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
								href={fileHref(org, file.key)}
							>
								<FileIcon class="size-4 flex-none text-muted-foreground" />
								<span class="truncate">{file.name}</span>
							</a>
						</td>
						<td class="hidden px-2 text-muted-foreground sm:table-cell">—</td>
						<td class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell">
							{bytes(file.size)}
						</td>
						<td
							class="px-2 text-muted-foreground tabular-nums"
							title={dateTime(file.last_modified)}
						>
							{relativeTime(file.last_modified, now)}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
		{#if failed}
			<p class="text-[13px] text-destructive" role="alert">{failed}</p>
		{/if}
		{#if cursor}
			<div>
				<Button variant="outline" size="sm" disabled={loading} onclick={more}>
					{loading ? 'Loading…' : 'Load more'}
				</Button>
			</div>
		{/if}
	{/if}
</div>
