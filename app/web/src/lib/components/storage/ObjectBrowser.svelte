<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import FileIcon from '@lucide/svelte/icons/file';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import DataTable, { type Column, ROW_LINK } from '#lib/components/DataTable.svelte';
	import BarSearch from '#lib/components/BarSearch.svelte';
	import ListPage from '#lib/components/ListPage.svelte';
	import { Selection } from '#lib/selection.svelte.ts';
	import { PAGE, bucketCall, entries, nameOf, objectHref, s3Uri, typeLabel } from '#lib/storage.ts';
	import { DATASETS_BUCKET, PROJECTS, datasetsHref } from '#lib/datasets.ts';
	import { headerTrail, type Crumb } from '#lib/storage.ts';
	import type { Listing, Presigned, ProjectInfo } from '#lib/types.ts';
	import {
		UploadQueue,
		browserTransport,
		droppedFiles,
		pickedFiles,
		type Picked
	} from '#lib/uploads.svelte.ts';
	import { MODELS_BUCKET, modelsHref } from '#lib/models.ts';
	import { bytes, count, dateTime, relativeTime } from '#lib/utils.ts';
	import CopyButton from '../CopyButton.svelte';
	import CreateFolderDialog from './CreateFolderDialog.svelte';
	import DeleteObjectsDialog from './DeleteObjectsDialog.svelte';
	import UploadList from './UploadList.svelte';

	interface Props {
		org: string;
		bucket: string;
		/** The folder shown: empty for the bucket's root, else ending in `/`. */
		prefix: string;
		/** Its first page, from the server. */
		listing: Listing;
		now: number;
		/** The org's projects, to link each `-projects/{id}/` folder to its project (D169). */
		projects?: readonly ProjectInfo[];
		/** The page's title: the bucket's name unless given. */
		title?: string;
		/** The page's breadcrumbs, under the title. */
		crumbs?: Crumb[];
	}

	let {
		org,
		bucket,
		prefix,
		listing,
		now,
		projects = [],
		title = bucket,
		crumbs = []
	}: Props = $props();

	// In `-projects/` of flock-datasets and flock-models each folder is a project's, by its id
	// (D160): it links to that project's Datasets or Models tab.
	const projectFolders = $derived(
		prefix === `${PROJECTS}/` && (bucket === DATASETS_BUCKET || bucket === MODELS_BUCKET)
	);
	const byId = $derived(new Map(projects.map((p) => [String(p.id), p.name])));
	function projectLink(key: string) {
		const name = projectFolders ? byId.get(nameOf(key)) : undefined;
		if (name === undefined) return null;
		const scope = { org, project: name };
		const tab = bucket === MODELS_BUCKET ? 'Models' : 'Datasets';
		const href = bucket === MODELS_BUCKET ? modelsHref(scope) : datasetsHref(scope);
		return { name, tab, href };
	}

	/** flock-models changes only through `flock models push` and the Models tab (D97). */
	const readOnly = $derived(bucket === MODELS_BUCKET);

	// The listing: the server's first page, then pages loaded here ("Load more", the filter,
	// a refresh). A new folder starts over from its own first page.
	let folders = $derived(listing.folders);
	let objects = $derived(listing.objects);
	let cursor = $derived(listing.cursor);
	let filter = $derived.by(() => {
		void prefix;
		return '';
	});
	let loading = $state(false);
	let failed = $state<string | null>(null);
	const rows = $derived(entries(folders, objects));
	const keys = $derived(rows.map((r) => r.key));

	let asked = 0; // the latest request, so an older answer can't replace a newer one
	async function load(more = false) {
		const ask = ++asked;
		loading = true;
		failed = null;
		const query: Record<string, string> = { prefix: prefix + filter, limit: String(PAGE) };
		if (more && cursor) query.cursor = cursor;
		try {
			const page = await bucketCall<Listing>(org, bucket, `objects?${new URLSearchParams(query)}`);
			if (ask !== asked) return;
			folders = more ? [...folders, ...page.folders] : page.folders;
			objects = more ? [...objects, ...page.objects] : page.objects;
			cursor = page.cursor;
			if (!more) {
				const shown = new Set(entries(folders, objects).map((r) => r.key));
				selection.drop(selection.chosen.filter((k) => !shown.has(k)));
			}
		} catch (e) {
			if (ask === asked) failed = (e as Error).message;
		} finally {
			if (ask === asked) loading = false;
		}
	}

	let typing: ReturnType<typeof setTimeout> | undefined;
	function filtered() {
		clearTimeout(typing);
		typing = setTimeout(() => load(), 250);
	}

	// Selection: a click toggles a row, a shift-click the range from the last click. A new
	// folder starts with nothing checked. Its rows are given at once, not only from the effect:
	// the header's checkbox stays disabled if its first render finds none.
	const selection = $derived.by(() => {
		void prefix;
		const fresh = new Selection();
		fresh.keys = untrack(() => keys);
		return fresh;
	});
	$effect.pre(() => {
		selection.keys = keys;
	});
	const chosenKeys = $derived(selection.chosen.filter((k) => !k.endsWith('/')));
	const chosenFolders = $derived(selection.chosen.filter((k) => k.endsWith('/')));

	const COLUMNS: Column[] = [
		{ id: 'name', label: 'Name' },
		{ id: 'type', label: 'Type', from: 'md' },
		{ id: 'size', label: 'Size', align: 'end', from: 'sm' },
		{ id: 'modified', label: 'Last modified', align: 'end', from: 'sm' }
	];

	// Uploads, and refreshing the listing once they're done.
	const queue = $derived(new UploadQueue(browserTransport(org, bucket), () => load()));
	let picker = $state<HTMLInputElement>();
	let dragging = $state(0);
	function upload(files: Picked[]) {
		queue.add(files.map(({ path, file }) => ({ key: prefix + path, file })));
	}

	async function download(key: string) {
		try {
			const { url } = await bucketCall<Presigned>(org, bucket, 'presign', { method: 'get', key });
			window.location.assign(url!);
		} catch (e) {
			toast.error((e as Error).message);
		}
	}

	let creatingFolder = $state(false);
	let folderButton = $state<HTMLElement | null>(null);
	// The header leads with the org: Storage › bucket › folders, the last the title.
	const header = $derived(headerTrail(crumbs, title));
	let deleting = $state(false);
</script>

{#snippet actions()}
	{#if !readOnly}
		<Button
			variant={creatingFolder ? 'quiet-on' : 'quiet'}
			size="bar"
			bind:ref={folderButton}
			onclick={() => (creatingFolder = !creatingFolder)}
		>
			<FolderPlusIcon /> <span class="max-sm:sr-only">Create folder</span>
		</Button>
		<Button variant="quiet" size="bar" onclick={() => picker?.click()}>
			<UploadIcon /> <span class="max-sm:sr-only">Upload</span>
		</Button>
	{/if}
	<input
		bind:this={picker}
		type="file"
		multiple
		hidden
		onchange={(e) => {
			const input = e.currentTarget;
			if (input.files?.length) upload(pickedFiles(input.files));
			input.value = '';
		}}
	/>
{/snippet}

<!-- While rows are checked: what to do with them. -->
{#snippet selected()}
	{#if chosenKeys.length === 1 && !chosenFolders.length}
		<Button variant="outline" size="sm" onclick={() => download(chosenKeys[0])}>
			<DownloadIcon /> Download
		</Button>
	{/if}
	{#if !readOnly}
		<Button variant="outline" size="sm" class="text-destructive" onclick={() => (deleting = true)}>
			<Trash2Icon /> Delete
		</Button>
	{/if}
{/snippet}

<!-- The folder's S3 URI to copy, and the search by name prefix: the bar's search, as on Runs. -->
{#snippet controls()}
	<!-- The trail already says where this is; the URI is for copying, named in the hint. -->
	<CopyButton text={s3Uri(bucket, prefix)} label="Copy {s3Uri(bucket, prefix)}" />
	<BarSearch
		bind:value={filter}
		things="objects"
		placeholder="Search by prefix…"
		oninput={filtered}
	/>
{/snippet}

<!-- Files dropped anywhere on the listing upload into this folder; folders keep their paths. -->
<ListPage
	title={header.title}
	trail={header.trail}
	org={false}
	{actions}
	{controls}
	{selection}
	{selected}
	class={dragging ? 'outline-2 -outline-offset-4 outline-primary outline-dashed' : undefined}
	aria-label="Objects in {s3Uri(bucket, prefix)}"
	ondragenter={(e) => {
		if (readOnly || !e.dataTransfer?.types.includes('Files')) return;
		e.preventDefault();
		dragging++;
	}}
	ondragover={(e) => {
		if (!readOnly && e.dataTransfer?.types.includes('Files')) e.preventDefault();
	}}
	ondragleave={() => (dragging = Math.max(0, dragging - 1))}
	ondrop={async (e) => {
		e.preventDefault();
		dragging = 0;
		if (e.dataTransfer && !readOnly) upload(await droppedFiles(e.dataTransfer));
	}}
>
	{#if readOnly || queue.uploads.length}
		<div class="grid gap-3 px-5 pt-4 pb-1">
			{#if readOnly}
				<p class="text-[13px] text-muted-foreground">
					Versions are added with <code class="font-mono text-[0.9em]">flock models push</code> and
					deleted in
					<a class="text-link underline-offset-3 hover:underline" href={modelsHref(org)}>Models</a>.
				</p>
			{/if}
			<UploadList {queue} />
		</div>
	{/if}

	<DataTable
		columns={COLUMNS}
		{rows}
		key={(row) => row.key}
		name={(row) => nameOf(row.key)}
		{selection}
		label="Objects"
		dense
		empty={failed || loading ? undefined : none}
		footer={failed || rows.length || cursor ? more : undefined}
	>
		{#snippet lead(row)}
			{#if row.kind === 'folder'}<FolderIcon />{:else}<FileIcon />{/if}
		{/snippet}
		{#snippet cell(row, column)}
			{#if column === 'name'}
				{@const link = row.kind === 'folder' ? projectLink(row.key) : null}
				<div class="flex min-w-0 items-center gap-3">
					<span class="flex min-w-0 items-center gap-2">
						<a
							class={['truncate hover:text-link', ROW_LINK]}
							href={objectHref(org, bucket, row.key)}
							{@attach hint(row.key)}
						>
							{nameOf(row.key)}
						</a>
					</span>
					{#if link}
						<a
							class="relative z-1 min-w-0 truncate text-[12.5px] text-muted-foreground underline-offset-3 hover:text-link hover:underline"
							href={link.href}>{link.name} in {link.tab}</a
						>
					{/if}
				</div>
			{:else if column === 'size'}
				<span class="text-muted-foreground">{row.kind === 'folder' ? '' : bytes(row.size)}</span>
			{:else if column === 'type'}
				<span class="block truncate text-muted-foreground">{typeLabel(row.key)}</span>
			{:else if row.kind === 'folder'}
				<!-- A folder has no size or date of its own. -->
			{:else}
				<span class="text-muted-foreground" {@attach hint(dateTime(row.last_modified))}>
					{relativeTime(row.last_modified, now)}
				</span>
			{/if}
		{/snippet}
	</DataTable>
</ListPage>

{#snippet none()}
	{filter
		? `Nothing here starts with “${filter}”.`
		: readOnly
			? 'This folder is empty.'
			: 'This folder is empty. Drop files here, or upload them.'}
{/snippet}

{#snippet more()}
	{#if failed}
		<p class="text-[13px] text-destructive" role="alert">{failed}</p>
	{/if}
	<!-- Counted only while there's more to load. -->
	{#if rows.length && cursor}
		<span class="text-[13px] text-muted-foreground tabular-nums">
			{count(rows.length, 'entry', 'entries')} so far
		</span>
	{/if}
	{#if cursor}
		<Button variant="outline" size="sm" onclick={() => load(true)} pending={loading}>
			Load more
		</Button>
	{/if}
{/snippet}

<CreateFolderDialog
	bind:open={creatingFolder}
	anchor={folderButton}
	{org}
	{bucket}
	{prefix}
	oncreated={() => load()}
/>
<DeleteObjectsDialog
	bind:open={deleting}
	{org}
	{bucket}
	keys={chosenKeys}
	prefixes={chosenFolders}
	ondeleted={() => {
		selection.clear();
		load();
	}}
/>
