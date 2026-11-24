<script lang="ts">
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { Input } from '#lib/components/ui/input/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { DatasetAt, DatasetEntry, DatasetFolder } from '#lib/types.ts';

	// A dropdown choosing a dataset of flock-datasets by browsing it a folder at a time, as the
	// Datasets tab lists it: folders open in place, a dataset is picked. The filter narrows the
	// folder shown. With `pick="folder"` it chooses a folder instead ('' is the bucket's root):
	// datasets aren't listed, and a button picks the folder open.

	interface Props {
		value: string;
		/** The scope browsed: the workspace's datasets, or a project's. */
		scope: Scope;
		/** Where browsing starts when nothing is picked yet. */
		folder?: string;
		/** What's chosen: a dataset (the default) or a folder. */
		pick?: 'dataset' | 'folder';
		disabled?: boolean;
		placeholder?: string;
		'aria-label'?: string;
	}

	let {
		value = $bindable(''),
		scope,
		folder = '',
		pick = 'dataset',
		disabled = false,
		placeholder = 'Choose a dataset',
		'aria-label': ariaLabel
	}: Props = $props();

	let open = $state(false);
	let at = $state('');
	let filter = $state('');
	let folders = $state<{ name: string; path: string }[]>([]);
	let datasets = $state<DatasetEntry[]>([]);
	let cursor = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let list = $state<HTMLElement | null>(null);
	let controller: AbortController | null = null;

	const parentOf = (path: string) => path.split('/').slice(0, -1).join('/');

	async function load(path: string, more = false) {
		controller?.abort();
		const mine = (controller = new AbortController());
		loading = true;
		error = null;
		if (!more) {
			at = path;
			filter = '';
			folders = [];
			datasets = [];
			cursor = null;
		}
		try {
			const page = await datasetCall<DatasetFolder | DatasetAt>(
				datasetsApi(scope, 'at', { path, cursor: more ? cursor : null }),
				{ signal: mine.signal }
			);
			if (page.kind === 'dataset') {
				// The folder started in is itself a dataset: browse its parent instead.
				if (path) return load(parentOf(path));
				return;
			}
			folders = [...folders, ...page.folders];
			datasets = [...datasets, ...page.datasets];
			cursor = page.cursor;
		} catch (e) {
			if (mine.signal.aborted) return;
			if (!more && path && (e as { status?: number }).status === 404) return load(parentOf(path));
			error = e instanceof Error ? e.message : String(e);
		} finally {
			if (controller === mine) loading = false;
		}
	}

	function onOpenChange(next: boolean) {
		if (!next) return;
		const picked = value.trim().replace(/^\/+|\/+$/g, '');
		if (pick === 'folder') void load(picked);
		else void load(picked ? parentOf(picked) : folder);
	}

	function choose(d: DatasetEntry) {
		value = d.path;
		open = false;
	}

	const q = $derived(filter.trim().toLowerCase());
	const shownFolders = $derived(folders.filter((f) => f.name.toLowerCase().includes(q)));
	const shownDatasets = $derived(
		pick === 'folder' ? [] : datasets.filter((d) => d.name.toLowerCase().includes(q))
	);
	const crumbs = $derived(at ? at.split('/') : []);

	/** Arrow keys move between the filter and the list's entries. */
	function onkeydown(e: KeyboardEvent) {
		if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
		const items = [
			...(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[data-item]')
		];
		if (!items.length) return;
		e.preventDefault();
		const i = items.indexOf(document.activeElement as HTMLElement);
		const next = e.key === 'ArrowDown' ? (i < 0 ? 0 : i + 1) : i - 1;
		if (next < 0) (e.currentTarget as HTMLElement).querySelector('input')?.focus();
		else items[Math.min(next, items.length - 1)].focus();
	}
</script>

<Popover.Root bind:open {onOpenChange}>
	<Popover.Trigger {disabled}>
		{#snippet child({ props })}
			<button
				{...props}
				type="button"
				aria-label={ariaLabel}
				class="flex h-8 w-full min-w-0 items-center gap-2 rounded-lg border border-input bg-transparent px-2.5 text-left text-sm transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 dark:bg-input/30"
			>
				{#if pick === 'folder'}
					<FolderIcon class="size-3.5 shrink-0 text-muted-foreground" />
					<span class="min-w-0 flex-1 truncate font-mono text-[12.5px]" title={value}
						>{value || 'flock-datasets'}</span
					>
				{:else if value}
					<DatabaseIcon class="size-3.5 shrink-0 text-muted-foreground" />
					<span class="min-w-0 flex-1 truncate font-mono text-[12.5px]" title={value}>{value}</span>
				{:else}
					<span class="min-w-0 flex-1 truncate text-muted-foreground">{placeholder}</span>
				{/if}
				<ChevronDownIcon class="size-4 shrink-0 text-muted-foreground" />
			</button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content
		align="start"
		class="w-(--bits-popover-anchor-width) min-w-80 gap-1.5 p-1.5"
		{onkeydown}
	>
		<Input
			class="h-8"
			placeholder={at ? `Filter ${at}` : 'Filter'}
			aria-label="Filter datasets"
			bind:value={filter}
			spellcheck="false"
			autocomplete="off"
			onkeydown={(e) => {
				if (e.key === 'Enter' && shownDatasets.length + shownFolders.length === 1) {
					e.preventDefault();
					if (shownDatasets[0]) choose(shownDatasets[0]);
					else void load(shownFolders[0].path);
				}
			}}
		/>
		<nav
			aria-label="Folder"
			class="flex min-w-0 items-center gap-0.5 px-1 text-xs text-muted-foreground"
		>
			<button
				type="button"
				aria-label="Up a folder"
				class="rounded p-0.5 hover:bg-accent disabled:opacity-40"
				disabled={!at}
				onclick={() => load(parentOf(at))}><ArrowUpIcon class="size-3.5" /></button
			>
			<button type="button" class="rounded px-1 hover:bg-accent" onclick={() => load('')}
				>flock-datasets</button
			>
			{#each crumbs as name, i (i)}
				<ChevronRightIcon class="size-3 shrink-0" />
				<button
					type="button"
					class="min-w-0 truncate rounded px-1 hover:bg-accent"
					onclick={() => load(crumbs.slice(0, i + 1).join('/'))}>{name}</button
				>
			{/each}
		</nav>
		<div
			bind:this={list}
			role="listbox"
			aria-label="Datasets in {at || 'flock-datasets'}"
			class="max-h-72 overflow-y-auto"
		>
			{#each shownFolders as f (f.path)}
				<button
					type="button"
					data-item
					class="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-sm outline-none hover:bg-accent focus-visible:bg-accent"
					onclick={() => load(f.path)}
				>
					<FolderIcon class="size-3.5 shrink-0 text-muted-foreground" />
					<span class="min-w-0 flex-1 truncate">{f.name}/</span>
					<ChevronRightIcon class="size-3.5 shrink-0 text-muted-foreground" />
				</button>
			{/each}
			{#each shownDatasets as d (d.path)}
				<button
					type="button"
					data-item
					role="option"
					aria-selected={d.path === value}
					class="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-sm outline-none hover:bg-accent focus-visible:bg-accent aria-selected:bg-secondary"
					onclick={() => choose(d)}
				>
					<DatabaseIcon class="size-3.5 shrink-0 text-muted-foreground" />
					<span class="min-w-0 flex-1 truncate">{d.name}</span>
					{#if d.origin}
						<span class="rounded bg-secondary px-1 text-[11px] text-muted-foreground"
							>{d.origin}</span
						>
					{/if}
					{#if d.version !== null}
						<span class="shrink-0 text-xs text-muted-foreground tabular-nums">v{d.version}</span>
					{/if}
				</button>
			{/each}
			{#if loading}
				<p class="flex items-center gap-2 px-1.5 py-1.5 text-xs text-muted-foreground">
					<LoaderCircleIcon class="size-3.5 animate-spin" /> Loading…
				</p>
			{:else if error}
				<p class="px-1.5 py-1.5 text-xs text-destructive">{error}</p>
			{:else if cursor}
				<button
					type="button"
					data-item
					class="w-full rounded-md px-1.5 py-1 text-left text-xs text-link outline-none hover:bg-accent focus-visible:bg-accent"
					onclick={() => load(at, true)}>Show more</button
				>
			{:else if !shownFolders.length && !shownDatasets.length}
				<p class="px-1.5 py-1.5 text-xs text-muted-foreground">
					{q
						? `Nothing here matches “${filter.trim()}”.`
						: pick === 'folder'
							? 'No folders inside.'
							: 'No datasets here.'}
				</p>
			{/if}
		</div>
		{#if pick === 'folder'}
			<button
				type="button"
				data-item
				class="flex w-full items-center gap-2 rounded-md border-t px-1.5 pt-1.5 pb-1 text-left text-[13px] outline-none hover:bg-accent focus-visible:bg-accent"
				onclick={() => {
					value = at;
					open = false;
				}}
			>
				<FolderIcon class="size-3.5 shrink-0 text-muted-foreground" />
				<span class="min-w-0 flex-1 truncate"
					>Put it in <span class="font-mono text-[12.5px]">{at || 'flock-datasets'}</span></span
				>
			</button>
		{/if}
	</Popover.Content>
</Popover.Root>
