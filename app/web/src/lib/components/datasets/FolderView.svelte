<script lang="ts">
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FileIcon from '@lucide/svelte/icons/file';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import { untrack } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { Button } from '#lib/components/ui/button/index.js';
	import {
		DATASETS_BUCKET,
		type DatasetItem,
		datasetCall,
		datasetsApi,
		datasetsHref,
		fileHref,
		parentOf,
		within,
		writeSnippet
	} from '#lib/datasets.ts';
	import type { Selection } from '#lib/selection.svelte.ts';
	import type { DatasetFolder, DeleteResult, MoveResult } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import RowCheckbox from '../RowCheckbox.svelte';
	import RowMenu from '../RowMenu.svelte';
	import DeleteDatasetsDialog from './DeleteDatasetsDialog.svelte';
	import MoveDatasetsDialog from './MoveDatasetsDialog.svelte';
	import RenameDialog from './RenameDialog.svelte';

	interface Props {
		org: string;
		/** The folder shown: empty for the bucket's root. */
		path: string;
		/** Its first page, from the server. */
		folder: DatasetFolder;
		now: number;
		/**
		 * Rows checked, for members and admins (D130), whose rows also get a menu (D133). The
		 * page shows the selection bar, whose actions open this listing's dialogs.
		 */
		selection?: Selection;
	}

	let { org, path, folder, now, selection }: Props = $props();

	// The server's first page, then pages loaded here with "Load more". Datasets come first in
	// each page, then folders, then files (D87).
	let datasets = $derived(folder.datasets);
	let folders = $derived(folder.folders);
	let files = $derived(folder.files);
	let cursor = $derived(folder.cursor);
	let loading = $state(false);
	let failed = $state<string | null>(null);
	const empty = $derived(!datasets.length && !folders.length && !files.length);

	// Datasets and folders can be checked; loose files can't (D130).
	const keys = $derived([...datasets.map((d) => d.path), ...folders.map((f) => f.path)]);
	$effect.pre(() => {
		if (selection) selection.keys = keys;
	});
	const folderPaths = $derived(new Set(folders.map((f) => f.path)));
	const itemOf = (p: string): DatasetItem => ({ path: p, folder: folderPaths.has(p) });
	// What the open dialog acts on, as it was when opened: its rows may leave the listing first.
	let targets = $state.raw<DatasetItem[]>([]);
	$effect.pre(() => {
		const t = selection?.targets ?? [];
		targets = untrack(() => t.map(itemOf));
	});

	/** Rows that left this folder: out of the listing and the selection. */
	function gone(paths: string[]) {
		if (!paths.length) return;
		const out = new Set(paths);
		datasets = datasets.filter((d) => !out.has(d.path));
		folders = folders.filter((f) => !out.has(f.path));
		selection?.drop(paths);
	}

	/**
	 * After a move or rename: rows moved within this folder read it again, so they show where
	 * they went; the others just leave. A folder emptied isn't read again, since S3 has no
	 * empty folders and it would be gone.
	 */
	async function landed(from: string[], to: string[]) {
		gone(from);
		if (to.some((t) => within(parentOf(t), path))) await invalidateAll();
	}

	async function moved(results: MoveResult[]) {
		const done = results.filter((r) => r.moved);
		await landed(
			done.map((r) => r.path),
			done.map((r) => r.to)
		);
	}

	const deleted = (results: DeleteResult[]) =>
		gone(results.filter((r) => r.deleted).map((r) => r.path));

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
		<table class="group/table w-full table-fixed border-collapse text-[13.5px]">
			<thead>
				<tr class="h-10 border-b text-left text-xs font-medium text-muted-foreground">
					{#if selection}
						<th class="w-8 pl-2"><RowCheckbox {selection} /></th>
					{/if}
					<th class="px-2 font-medium">Name</th>
					<th class="hidden w-36 px-2 font-medium sm:table-cell">Version</th>
					<th class="hidden w-36 px-2 font-medium sm:table-cell">Size</th>
					<th class="w-36 px-2 font-medium">Updated</th>
					{#if selection}<th class="w-9"><span class="sr-only">Actions</span></th>{/if}
				</tr>
			</thead>
			<tbody>
				{#each datasets as d (`d:${d.path}`)}
					{@const on = !!selection?.has(d.path)}
					<tr
						class={[
							'group/row h-10 border-b border-divider',
							on ? 'bg-selection/45' : 'hover:bg-muted'
						]}
						aria-selected={selection ? on : undefined}
					>
						{#if selection}
							<td class="pl-2"><RowCheckbox {selection} key={d.path} name={d.name} /></td>
						{/if}
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
						{#if selection}
							<td class="pr-1 text-right">
								<RowMenu
									name={d.name}
									onrename={() => selection.act('rename', [d.path])}
									ondelete={() => selection.act('delete', [d.path])}
								/>
							</td>
						{/if}
					</tr>
				{/each}
				{#each folders as f (`f:${f.path}`)}
					{@const on = !!selection?.has(f.path)}
					<tr
						class={[
							'group/row h-10 border-b border-divider',
							on ? 'bg-selection/45' : 'hover:bg-muted'
						]}
						aria-selected={selection ? on : undefined}
					>
						{#if selection}
							<td class="pl-2"><RowCheckbox {selection} key={f.path} name="{f.name}/" /></td>
						{/if}
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
						{#if selection}
							<td class="pr-1 text-right">
								<RowMenu
									name="{f.name}/"
									onrename={() => selection.act('rename', [f.path])}
									ondelete={() => selection.act('delete', [f.path])}
								/>
							</td>
						{/if}
					</tr>
				{/each}
				{#each files as file (`o:${file.key}`)}
					<tr class="h-10 border-b border-divider hover:bg-muted">
						{#if selection}<td></td>{/if}
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
						{#if selection}<td></td>{/if}
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

{#if selection}
	{@const one = targets[0] ?? { path: '', folder: false }}
	<MoveDatasetsDialog
		bind:open={() => selection.action === 'move', (o) => o || selection.close('move')}
		{org}
		from={path}
		items={targets}
		onmoved={moved}
	/>
	<DeleteDatasetsDialog
		bind:open={() => selection.action === 'delete', (o) => o || selection.close('delete')}
		{org}
		items={targets}
		ondeleted={deleted}
	/>
	<RenameDialog
		bind:open={() => selection.action === 'rename', (o) => o || selection.close('rename')}
		{org}
		path={one.path}
		folder={one.folder}
		onrenamed={(to) => landed([one.path], [to])}
	/>
{/if}
