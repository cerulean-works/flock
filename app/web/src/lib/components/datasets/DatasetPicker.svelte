<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import PickerField from '#lib/components/PickerField.svelte';
	import PickerList from '#lib/components/PickerList.svelte';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { DatasetAt, DatasetEntry, DatasetFolder } from '#lib/types.ts';

	/**
	 * Chooses a dataset of flock-datasets by browsing it a folder at a time, as the Datasets tab
	 * lists it: folders open in place, a dataset is picked, the search narrows the folder shown.
	 * With `pick="folder"` it chooses a folder instead ('' is the bucket's root): datasets aren't
	 * listed, and a button picks the folder open.
	 */
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

	/** A folder to open, or a dataset to pick. */
	interface Row {
		name: string;
		path: string;
		dataset: DatasetEntry | null;
	}

	let open = $state(false);
	let at = $state('');
	// The folder this opening started in, which Escape goes back to.
	let start = $state<string | null>(null);
	let query = $state('');
	let active = $state(0);
	let folders = $state<{ name: string; path: string }[]>([]);
	let datasets = $state<DatasetEntry[]>([]);
	let cursor = $state<string | null>(null);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let controller: AbortController | null = null;

	const parentOf = (path: string) => path.split('/').slice(0, -1).join('/');

	// Folders first, then the datasets, both narrowed by the search.
	const rows = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const shown: Row[] = [];
		for (const f of folders) {
			if (f.name.toLowerCase().includes(q)) shown.push({ ...f, dataset: null });
		}
		if (pick === 'folder') return shown;
		for (const d of datasets) {
			if (d.name.toLowerCase().includes(q)) shown.push({ name: d.name, path: d.path, dataset: d });
		}
		return shown;
	});
	const crumbs = $derived(at ? at.split('/') : []);

	async function load(path: string, more = false) {
		controller?.abort();
		const mine = (controller = new AbortController());
		loading = true;
		error = null;
		if (!more) {
			at = path;
			query = '';
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
			if (more) return;
			start ??= path;
			// The folder of the dataset picked opens on it.
			active = Math.max(
				0,
				rows.findIndex((r) => r.dataset && r.path === value)
			);
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
		start = null;
		if (pick === 'folder') void load(picked);
		else void load(picked ? parentOf(picked) : folder);
	}

	function onpick(row: Row) {
		if (!row.dataset) {
			void load(row.path);
			return;
		}
		value = row.path;
		open = false;
	}
</script>

<PickerField bind:open {onOpenChange} {disabled} aria-label={ariaLabel}>
	{#if pick === 'folder'}
		<FolderIcon class="size-3.5 shrink-0 text-muted-foreground" />
		<span class="min-w-0 flex-1 truncate font-mono text-[12.5px]" {@attach hint(value)}
			>{value || 'flock-datasets'}</span
		>
	{:else if value}
		<DatabaseIcon class="size-3.5 shrink-0 text-muted-foreground" />
		<span class="min-w-0 flex-1 truncate font-mono text-[12.5px]" {@attach hint(value)}
			>{value}</span
		>
	{:else}
		<span class="min-w-0 flex-1 truncate text-muted-foreground">{placeholder}</span>
	{/if}
	{#snippet list()}
		<nav
			aria-label="Folder"
			class="flex min-w-0 items-center gap-0.5 border-b border-divider px-2 py-1 text-xs text-muted-foreground"
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
		<PickerList
			options={rows}
			key={(r) => r.path}
			current={pick === 'dataset' ? (r) => r.dataset !== null && r.path === value : undefined}
			noun={pick === 'folder' ? 'folders' : 'datasets'}
			label={pick === 'folder' ? 'Search folders' : 'Search datasets'}
			placeholder={pick === 'folder' ? 'Search folders…' : 'Search datasets…'}
			bind:query
			bind:active
			{loading}
			{error}
			verb={rows[active]?.dataset === null ? 'open' : 'pick'}
			{onpick}
			onup={at ? () => load(parentOf(at)) : undefined}
			onback={start !== null && at !== start ? () => load(start ?? '') : undefined}
		>
			{#snippet row(r: Row)}
				{#if r.dataset}
					<DatabaseIcon class="size-3.5 shrink-0 text-muted-foreground" />
					<span class="min-w-0 flex-1 truncate">{r.name}</span>
					{#if r.dataset.origin}
						<span class="rounded bg-secondary px-1 text-[11px] text-muted-foreground"
							>{r.dataset.origin}</span
						>
					{/if}
					{#if r.dataset.version !== null}
						<span class="shrink-0 text-xs text-muted-foreground tabular-nums"
							>v{r.dataset.version}</span
						>
					{/if}
				{:else}
					<FolderIcon class="size-3.5 shrink-0 text-muted-foreground" />
					<span class="min-w-0 flex-1 truncate">{r.name}/</span>
					<ChevronRightIcon class="size-3.5 shrink-0 text-muted-foreground" />
				{/if}
			{/snippet}
			{#snippet actions()}
				{#if cursor && !loading && !error}
					<button
						type="button"
						class="mx-1 mb-1 rounded-md px-2 py-1 text-left text-xs text-link outline-none hover:bg-accent focus-visible:bg-accent"
						onclick={() => load(at, true)}>Show more</button
					>
				{/if}
				{#if pick === 'folder'}
					<button
						type="button"
						class="flex w-full items-center gap-2 border-t border-divider px-3 py-1.5 text-left text-[13px] outline-none hover:bg-accent focus-visible:bg-accent"
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
			{/snippet}
		</PickerList>
	{/snippet}
</PickerField>
