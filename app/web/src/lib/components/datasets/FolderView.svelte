<script lang="ts">
	import { accountHref } from '#lib/settings.ts';
	import { hint } from '#lib/hint.svelte.ts';
	import type { Scope } from '#lib/scope.ts';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FileIcon from '@lucide/svelte/icons/file';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { bucketCall } from '#lib/storage.ts';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import {
		DATASETS_BUCKET,
		type DatasetItem,
		datasetCall,
		datasetsApi,
		datasetsHref,
		fileHref,
		parentOf,
		within
	} from '#lib/datasets.ts';
	import { datasetSnippet } from '#lib/snippets.ts';
	import type { Selection } from '#lib/selection.svelte.ts';
	import type { DatasetFolder, DeleteResult, MoveResult, Presigned } from '#lib/types.ts';
	import { bytes, count, dateTime, relativeTime } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import EmptyState from '../EmptyState.svelte';
	import DataTable, { type Column, ROW_LINK } from '../DataTable.svelte';
	import RowMenuItems from '../RowMenuItems.svelte';
	import DeleteDatasetsDialog from './DeleteDatasetsDialog.svelte';
	import MoveDatasetsDialog from './MoveDatasetsDialog.svelte';
	import ForkDialog from './ForkDialog.svelte';
	import RenameDialog from './RenameDialog.svelte';
	import { openMenuTrigger } from '../AnchoredForm.svelte';

	interface Props {
		scope: Scope;
		/** The folder shown: empty for the bucket's root. */
		path: string;
		/** Its first page, from the server. */
		folder: DatasetFolder;
		now: number;
		/**
		 * Rows checked, for members and admins (D130), whose rows also get a menu (D133): a
		 * checked row's menu acts on every row checked.
		 */
		selection?: Selection;
		/** Blend these datasets (the page's blend dialog). */
		onblend?: (paths: readonly string[]) => void;
		/** The org's projects, for the Move dialog's scope picker (D166). */
		projects?: readonly string[];
	}

	let { scope, path, folder, now, selection, onblend, projects = [] }: Props = $props();
	/** The ⋯ menu a rename opened from. */
	let renameAnchor = $state<HTMLElement | null>(null);

	// The server's first page, then pages loaded here with "Load more". Folders come first, then
	// datasets, then files, as every list has them.
	let datasets = $derived(folder.datasets);
	let folders = $derived(folder.folders);
	let files = $derived(folder.files);
	let cursor = $derived(folder.cursor);
	let loading = $state(false);
	let failed = $state<string | null>(null);
	const empty = $derived(!datasets.length && !folders.length && !files.length);

	/** One line of the listing: a dataset, a folder or a loose file. */
	interface Row {
		kind: 'dataset' | 'folder' | 'file';
		/** A dataset's or folder's path, its selection key; a file's object key, prefixed. */
		key: string;
		name: string;
		href: string;
		origin?: string | null;
		/** Undefined for what has no version. */
		version?: number | null;
		size?: number | null;
		updated?: number | null;
	}
	const rows = $derived<Row[]>([
		...folders.map((f) => ({
			kind: 'folder' as const,
			key: f.path,
			name: f.name,
			href: datasetsHref(scope, f.path)
		})),
		...datasets.map((d) => ({
			kind: 'dataset' as const,
			key: d.path,
			name: d.name,
			href: datasetsHref(scope, d.path),
			origin: d.origin,
			version: d.version,
			size: d.size,
			updated: d.updated_at
		})),
		...files.map((f) => ({
			kind: 'file' as const,
			key: `o:${f.key}`,
			name: f.name,
			href: fileHref(scope.org, f.key),
			size: f.size,
			updated: f.last_modified
		}))
	]);
	const ICONS = { dataset: DatabaseIcon, folder: FolderIcon, file: FileIcon };
	// Version and size show from small windows up. A level of folders alone (the root) has none of
	// them to show, so it lists names only rather than three empty columns.
	const ALL_COLUMNS: Column[] = [
		{ id: 'name', label: 'Name' },
		{ id: 'version', label: 'Version', from: 'sm', align: 'end' },
		{ id: 'size', label: 'Size', from: 'sm', align: 'end' },
		{ id: 'updated', label: 'Updated', align: 'end' }
	];
	const COLUMNS = $derived(datasets.length || files.length ? ALL_COLUMNS : ALL_COLUMNS.slice(0, 1));

	// Datasets and folders can be checked; loose files can't (D130).
	const keys = $derived([...folders.map((f) => f.path), ...datasets.map((d) => d.path)]);
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

	/** Rows moved to another scope just leave; they're nowhere in this one. */
	async function moved(results: MoveResult[], to: Scope) {
		const done = results.filter((r) => r.moved);
		if (to.project !== scope.project) return gone(done.map((r) => r.path));
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
			const page = await datasetCall<DatasetFolder>(
				datasetsApi(scope, 'at', { path, cursor, details: 'true' })
			);
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

	async function download(key: string) {
		try {
			const { url } = await bucketCall<Presigned>(scope.org, DATASETS_BUCKET, 'presign', {
				method: 'get',
				key
			});
			window.location.assign(url!);
		} catch (e) {
			toast.error((e as Error).message);
		}
	}
</script>

{#if empty && !path}
	<EmptyState
		variant="onboarding"
		title={scope.project === null ? 'No datasets yet' : `No datasets in ${scope.project} yet`}
	>
		{#snippet lead()}
			{#if scope.project === null}
				Write a Lance dataset into <span class="font-mono text-[0.9em]">{DATASETS_BUCKET}</span> and it
				shows up here.
			{:else}
				Write a Lance dataset where <code class="font-mono text-[0.9em]">location</code> says.
			{/if}
		{/snippet}
		<CopyBlock lang="python" code={datasetSnippet(scope)} />
		{#snippet actions()}
			<span>
				Tools that can’t run <code class="font-mono text-[0.9em]">flock</code> can use a long-lived
				key from your
				<a class="text-link underline-offset-3 hover:underline" href="{accountHref('keys')}?keys=s3"
					>account page</a
				>.
			</span>
		{/snippet}
	</EmptyState>
{:else}
	<DataTable
		columns={COLUMNS}
		{rows}
		key={(row) => row.key}
		label="Datasets"
		dense
		{selection}
		selectable={(row) => row.kind !== 'file'}
		name={(row) => row.name}
		menu={rowMenu}
		hasMenu={(row) => row.kind === 'file' || !!selection}
		empty={nothing}
		footer={cursor || failed ? pager : undefined}
	>
		{#snippet lead(row)}
			{@const Icon = ICONS[row.kind]}
			<Icon />
		{/snippet}
		{#snippet cell(row, column)}
			{#if column === 'name'}
				<span class="flex min-w-0 items-center gap-2">
					<a
						class={['truncate underline-offset-3 hover:text-link hover:underline', ROW_LINK]}
						href={row.href}
					>
						{row.name}
					</a>
					{#if row.origin}
						<span class="flex-none rounded bg-muted px-1.5 text-[11px] text-muted-foreground">
							{row.origin}
						</span>
					{/if}
				</span>
			{:else if column === 'version'}
				<span class="text-muted-foreground">
					{row.version === undefined ? '' : row.version === null ? 'Unknown' : `v${row.version}`}
				</span>
			{:else if column === 'size'}
				<span class="text-muted-foreground">{row.size == null ? '' : bytes(row.size)}</span>
			{:else}
				<span
					class="text-muted-foreground"
					{@attach hint(row.updated ? dateTime(row.updated) : undefined)}
				>
					{row.updated ? relativeTime(row.updated, now) : ''}
				</span>
			{/if}
		{/snippet}
	</DataTable>
{/if}

{#snippet nothing()}This folder is empty.{/snippet}

{#snippet rowMenu(row: Row)}
	{#if row.kind === 'file'}
		<!-- A loose file isn't the list's to check or move (D130), but downloads as Storage's do. -->
		<RowMenuItems ondownload={() => download(row.key.slice(2))} />
	{:else if selection}
		{@const on = selection.targetsFor(row.key)}
		{@const sets = on.filter((k) => !folderPaths.has(k))}
		<RowMenuItems
			many={on.length < 2
				? null
				: sets.length === on.length
					? count(on.length, 'dataset')
					: sets.length
						? count(on.length, 'item')
						: count(on.length, 'folder')}
			onfork={row.kind === 'dataset' ? () => selection.act('fork', [row.key]) : undefined}
			onrename={() => {
				renameAnchor = openMenuTrigger();
				selection.act('rename', [row.key]);
			}}
			onblend={onblend && sets.length ? () => onblend(sets) : undefined}
			onmove={() => selection.act('move', on)}
			ondelete={() => selection.act('delete', on)}
		/>
	{/if}
{/snippet}

{#snippet pager()}
	{#if cursor}
		<Button variant="outline" onclick={more} pending={loading}>Load more</Button>
	{/if}
	{#if failed}
		<span class="text-[13px] text-destructive" role="alert">{failed}</span>
	{/if}
{/snippet}

{#if selection}
	{@const one = targets[0] ?? { path: '', folder: false }}
	<MoveDatasetsDialog
		bind:open={() => selection.action === 'move', (o) => o || selection.close('move')}
		{scope}
		from={path}
		items={targets}
		{projects}
		onmoved={moved}
	/>
	<DeleteDatasetsDialog
		bind:open={() => selection.action === 'delete', (o) => o || selection.close('delete')}
		{scope}
		items={targets}
		ondeleted={deleted}
	/>
	<ForkDialog
		bind:open={() => selection.action === 'fork', (o) => o || selection.close('fork')}
		{scope}
		path={one.path}
		{projects}
	/>
	<RenameDialog
		bind:open={() => selection.action === 'rename', (o) => o || selection.close('rename')}
		anchor={renameAnchor}
		{scope}
		path={one.path}
		folder={one.folder}
		onrenamed={(to) => landed([one.path], [to])}
	/>
{/if}
