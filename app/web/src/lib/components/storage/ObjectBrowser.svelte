<script lang="ts">
	import DownloadIcon from '@lucide/svelte/icons/download';
	import FileIcon from '@lucide/svelte/icons/file';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import SearchIcon from '@lucide/svelte/icons/search';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import {
		PAGE,
		bucketCall,
		clickSelect,
		entries,
		nameOf,
		objectHref,
		s3Uri,
		typeLabel
	} from '#lib/storage.ts';
	import type { Listing, Presigned } from '#lib/types.ts';
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
	}

	let { org, bucket, prefix, listing, now }: Props = $props();

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
			if (!more) selected = new Set([...selected].filter((k) => keys.includes(k)));
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

	// Selection: a click toggles a row, a shift-click the range from the last click.
	let selected = $derived.by(() => {
		void prefix;
		return new Set<string>();
	});
	let anchor: string | null = null;
	function select(key: string, shift: boolean) {
		selected = clickSelect(selected, keys, key, anchor, shift);
		anchor = key;
	}
	const allSelected = $derived(keys.length > 0 && keys.every((k) => selected.has(k)));
	const chosenKeys = $derived(keys.filter((k) => selected.has(k) && !k.endsWith('/')));
	const chosenFolders = $derived(keys.filter((k) => selected.has(k) && k.endsWith('/')));

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
	let deleting = $state(false);
</script>

<div class="grid gap-3">
	<div class="flex flex-wrap items-center gap-2">
		<div class="relative w-full max-w-72">
			<SearchIcon
				class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground"
			/>
			<input
				class="h-8 w-full rounded-lg border border-input bg-background pr-2.5 pl-8 text-[13px] outline-none placeholder:text-faint focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
				placeholder="Filter by name prefix"
				aria-label="Filter by name prefix"
				spellcheck="false"
				bind:value={filter}
				oninput={filtered}
			/>
		</div>
		<div class="ml-auto flex items-center gap-1">
			<span class="flex items-center gap-1 text-[13px] text-muted-foreground max-sm:hidden">
				<span class="max-w-60 truncate font-mono text-[12.5px]">{s3Uri(bucket, prefix)}</span>
				<CopyButton text={s3Uri(bucket, prefix)} label="Copy S3 URI" />
			</span>
			{#if !readOnly}
				<Button variant="outline" size="sm" onclick={() => (creatingFolder = true)}>
					<FolderPlusIcon /> Create folder
				</Button>
				<Button size="sm" onclick={() => picker?.click()}><UploadIcon /> Upload</Button>
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
		</div>
	</div>

	{#if readOnly}
		<p class="text-[13px] text-muted-foreground">
			Versions are added with <code class="font-mono text-[0.9em]">flock models push</code> and
			deleted in
			<a class="text-link underline-offset-3 hover:underline" href={modelsHref(org)}>Models</a>.
		</p>
	{/if}
	<UploadList {queue} />

	{#if selected.size}
		<div
			class="flex h-10 items-center gap-2 bg-card px-3 text-[13px]"
			role="toolbar"
			aria-label="Selection"
		>
			<span class="tabular-nums">{selected.size.toLocaleString('en-US')} selected</span>
			<Button
				variant="ghost"
				size="xs"
				class="text-muted-foreground"
				onclick={() => (selected = new Set())}
			>
				Clear
			</Button>
			<div class="ml-auto flex gap-1">
				{#if chosenKeys.length === 1 && !chosenFolders.length}
					<Button variant="outline" size="sm" onclick={() => download(chosenKeys[0])}>
						<DownloadIcon /> Download
					</Button>
				{/if}
				{#if !readOnly}
					<Button
						variant="outline"
						size="sm"
						class="text-destructive"
						onclick={() => (deleting = true)}
					>
						<Trash2Icon /> Delete
					</Button>
				{/if}
			</div>
		</div>
	{/if}

	<!-- Files dropped anywhere on the listing upload into this folder; folders keep their paths. -->
	<div
		class={['relative', dragging && 'outline-2 outline-offset-2 outline-primary outline-dashed']}
		role="region"
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
		<table class="w-full table-fixed border-collapse text-[13.5px]">
			<thead>
				<tr class="h-10 border-b text-left text-xs font-medium text-muted-foreground">
					<th class="w-9 pl-2">
						<Checkbox
							checked={allSelected}
							indeterminate={selected.size > 0 && !allSelected}
							disabled={!keys.length}
							onCheckedChange={() => (selected = allSelected ? new Set() : new Set(keys))}
							aria-label={allSelected ? 'Deselect all' : 'Select all shown'}
						/>
					</th>
					<th class="px-2 font-medium">Name</th>
					<th class="hidden w-24 px-2 text-right font-medium sm:table-cell">Size</th>
					<th class="hidden w-32 px-2 font-medium md:table-cell">Type</th>
					<th class="hidden w-36 px-2 font-medium sm:table-cell">Last modified</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as row (row.key)}
					{@const on = selected.has(row.key)}
					<tr
						class={['h-10 border-b border-divider', on ? 'bg-selection/45' : 'hover:bg-muted']}
						aria-selected={on}
					>
						<td class="pl-2">
							<Checkbox
								bind:checked={() => on, () => {}}
								onclick={(e: MouseEvent) => select(row.key, e.shiftKey)}
								aria-label="Select {nameOf(row.key)}"
							/>
						</td>
						<td class="truncate px-2">
							<a
								class="flex min-w-0 items-center gap-2 underline-offset-3 hover:text-link hover:underline"
								href={objectHref(org, bucket, row.key)}
								title={row.key}
							>
								{#if row.kind === 'folder'}
									<FolderIcon class="size-4 flex-none text-muted-foreground" />
									<span class="truncate">{nameOf(row.key)}/</span>
								{:else}
									<FileIcon class="size-4 flex-none text-muted-foreground" />
									<span class="truncate">{nameOf(row.key)}</span>
								{/if}
							</a>
						</td>
						<td class="hidden px-2 text-right text-muted-foreground tabular-nums sm:table-cell">
							{row.kind === 'folder' ? '—' : bytes(row.size)}
						</td>
						<td class="hidden truncate px-2 text-muted-foreground md:table-cell">
							{typeLabel(row.key)}
						</td>
						<td
							class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell"
							title={row.kind === 'folder' ? undefined : dateTime(row.last_modified)}
						>
							{row.kind === 'folder' ? '—' : relativeTime(row.last_modified, now)}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
		{#if failed}
			<p class="mt-3 text-[13px] text-destructive" role="alert">{failed}</p>
		{:else if !rows.length && !loading}
			<p class="mt-4 text-muted-foreground">
				{filter
					? `Nothing here starts with “${filter}”.`
					: 'This folder is empty. Drop files here, or upload them.'}
			</p>
		{/if}
		<div class="mt-3 flex items-center gap-3 text-[13px] text-muted-foreground">
			{#if rows.length}
				<span class="tabular-nums">
					{count(rows.length, 'entry', 'entries')}{cursor ? ' so far' : ''}
				</span>
			{/if}
			{#if cursor}
				<Button variant="outline" size="sm" disabled={loading} onclick={() => load(true)}>
					{loading ? 'Loading…' : 'Load more'}
				</Button>
			{/if}
		</div>
	</div>
</div>

<CreateFolderDialog bind:open={creatingFolder} {org} {bucket} {prefix} oncreated={() => load()} />
<DeleteObjectsDialog
	bind:open={deleting}
	{org}
	{bucket}
	keys={chosenKeys}
	prefixes={chosenFolders}
	ondeleted={() => {
		selected = new Set();
		load();
	}}
/>
