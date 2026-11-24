<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import FolderPlusIcon from '@lucide/svelte/icons/folder-plus';
	import { DATASETS_BUCKET, datasetCall, datasetsApi, pathError } from '#lib/datasets.ts';
	import { folderApi, modelNameError } from '#lib/models.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { DatasetFolder, ModelAt, ModelFolder } from '#lib/types.ts';

	interface Props {
		id: string;
		/** The folder picked, relative to the scope. */
		value: string;
		/** The scope browsed: the move's destination. */
		scope: Scope;
		kind: 'dataset' | 'model';
		/** Folders that can't be picked (a folder can't move into itself). */
		exclude?: (path: string) => boolean;
		disabled?: boolean;
	}

	let { id, value = $bindable(), scope, kind, exclude, disabled = false }: Props = $props();

	/** The folder picked, without slashes at either end. */
	const target = $derived(value.trim().replace(/^\/+|\/+$/g, ''));
	const invalid = (path: string) => (kind === 'dataset' ? pathError(path) : modelNameError(path));
	/** What the top of the scope is called. */
	const root = $derived(
		kind === 'dataset' && scope.project === null ? DATASETS_BUCKET : (scope.project ?? scope.org)
	);

	// The folders in the destination, read as it's typed, and again when the scope changes.
	let browsing = $state<string | null>(null);
	let folders = $state<{ name: string; path: string }[] | null>(null);
	$effect(() => {
		const at = target;
		const where = { ...scope };
		if (at && invalid(at)) {
			folders = [];
			return;
		}
		const controller = new AbortController();
		const timer = setTimeout(
			() => void list(where, at, controller.signal),
			at === browsing ? 0 : 200
		);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	// Folders come after a page's datasets or models, so a few pages are read to find them.
	const PAGES = 5;
	async function list(where: Scope, at: string, signal: AbortSignal) {
		const found: { name: string; path: string }[] = [];
		let cursor: string | null = null;
		try {
			for (let n = 0; n < PAGES; n++) {
				const page: DatasetFolder | ModelFolder | ModelAt =
					kind === 'dataset'
						? await datasetCall<DatasetFolder>(datasetsApi(where, 'at', { path: at, cursor }), {
								signal
							})
						: await datasetCall<ModelFolder | ModelAt>(folderApi(where, at, cursor), { signal });
				if (page.kind !== 'folder') break;
				found.push(...page.folders);
				cursor = page.cursor;
				if (!cursor) break;
			}
		} catch (e) {
			if ((e as Error).name === 'AbortError') return;
		}
		browsing = at;
		folders = found.filter((f) => !exclude?.(f.path));
	}

	/** Each folder on the way down, for the breadcrumbs. */
	const crumbs = $derived(
		target
			? target.split('/').map((name, i, all) => ({ name, path: all.slice(0, i + 1).join('/') }))
			: []
	);

	// A folder that doesn't exist yet is named in place, then picked like any other.
	let naming = $state(false);
	let draft = $state('');
	let draftError = $state<string | null>(null);
	function addFolder() {
		const name = draft.trim().replace(/^\/+|\/+$/g, '');
		if (!name) {
			naming = false;
			return;
		}
		const path = target ? `${target}/${name}` : name;
		draftError = name.includes('/') ? 'One folder at a time' : invalid(path);
		if (draftError) return;
		value = path;
		naming = false;
		draft = '';
	}
</script>

<div class="overflow-hidden rounded-lg border border-input text-[13px]">
	<!-- Where the items go: each step back up is a click away. -->
	<div
		{id}
		class="flex min-h-9 flex-wrap items-center gap-0.5 border-b border-input bg-muted/40 px-1.5 py-1"
		role="group"
		aria-label="Folder"
	>
		<button
			type="button"
			class={[
				'rounded px-1.5 py-0.5 hover:bg-muted',
				target ? 'text-muted-foreground' : 'font-medium text-foreground'
			]}
			{disabled}
			aria-current={target ? undefined : 'location'}
			onclick={() => (value = '')}
		>
			{root}
		</button>
		{#each crumbs as crumb, i (crumb.path)}
			<ChevronRightIcon class="size-3.5 flex-none text-faint" aria-hidden="true" />
			<button
				type="button"
				class={[
					'rounded px-1.5 py-0.5 hover:bg-muted',
					i === crumbs.length - 1 ? 'font-medium text-foreground' : 'text-muted-foreground'
				]}
				{disabled}
				aria-current={i === crumbs.length - 1 ? 'location' : undefined}
				onclick={() => (value = crumb.path)}
			>
				{crumb.name}
			</button>
		{/each}
	</div>
	<div class="h-44 overflow-y-auto py-1" role="group" aria-label="Folders">
		{#each folders ?? [] as f (f.path)}
			<button
				type="button"
				class="flex h-8 w-full items-center gap-2 px-2.5 text-left hover:bg-muted"
				{disabled}
				onclick={() => (value = f.path)}
			>
				<FolderIcon class="size-4 flex-none text-muted-foreground" />
				<span class="truncate">{f.name}/</span>
			</button>
		{:else}
			<p class="px-2.5 py-1.5 text-muted-foreground">
				{folders === null ? 'Loading folders…' : 'No folders here.'}
			</p>
		{/each}
		{#if naming}
			<div class="flex h-8 items-center gap-2 px-2.5">
				<FolderPlusIcon class="size-4 flex-none text-muted-foreground" />
				<!-- svelte-ignore a11y_autofocus -->
				<input
					class="h-6 min-w-0 flex-1 rounded border border-input bg-transparent px-1.5 outline-none focus-visible:border-ring"
					aria-label="New folder name"
					placeholder="e.g., archive"
					autocomplete="off"
					spellcheck="false"
					autofocus
					bind:value={draft}
					oninput={() => (draftError = null)}
					onkeydown={(e) => {
						if (e.key === 'Enter') {
							e.preventDefault();
							addFolder();
						} else if (e.key === 'Escape') {
							e.preventDefault();
							e.stopPropagation();
							naming = false;
						}
					}}
					onblur={addFolder}
				/>
			</div>
			{#if draftError}<p class="px-2.5 text-xs text-destructive" role="alert">{draftError}</p>{/if}
		{:else}
			<button
				type="button"
				class="flex h-8 w-full items-center gap-2 px-2.5 text-left text-muted-foreground hover:bg-muted"
				{disabled}
				onclick={() => {
					draft = '';
					draftError = null;
					naming = true;
				}}
			>
				<FolderPlusIcon class="size-4 flex-none" />
				New folder
			</button>
		{/if}
	</div>
</div>
