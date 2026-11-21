<script lang="ts">
	import CornerLeftUpIcon from '@lucide/svelte/icons/corner-left-up';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import { Input } from '#lib/components/ui/input/index.js';
	import {
		DATASETS_BUCKET,
		datasetCall,
		datasetsApi,
		nameOf,
		parentOf,
		pathError
	} from '#lib/datasets.ts';
	import { folderApi, modelNameError } from '#lib/models.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { DatasetFolder, ModelAt, ModelFolder } from '#lib/types.ts';

	interface Props {
		id: string;
		/** The folder typed or picked, relative to the scope. */
		value: string;
		/** The scope browsed: the move's destination. */
		scope: Scope;
		kind: 'dataset' | 'model';
		/** Folders that can't be picked (a folder can't move into itself). */
		exclude?: (path: string) => boolean;
		disabled?: boolean;
	}

	let { id, value = $bindable(), scope, kind, exclude, disabled = false }: Props = $props();

	/** The folder as typed, without slashes at either end. */
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
</script>

<Input
	{id}
	class="font-mono text-[13px]"
	bind:value
	placeholder="The top of {root}"
	{disabled}
	autocomplete="off"
	spellcheck="false"
/>
<div
	class="h-44 overflow-y-auto rounded-lg border border-input py-1 text-[13px]"
	role="group"
	aria-label="Folders"
>
	{#if target}
		<button
			type="button"
			class="flex h-8 w-full items-center gap-2 px-2.5 text-left text-muted-foreground hover:bg-muted"
			{disabled}
			onclick={() => (value = parentOf(target))}
		>
			<CornerLeftUpIcon class="size-4 flex-none" />
			<span class="truncate">{parentOf(target) ? nameOf(parentOf(target)) : root}</span>
		</button>
	{/if}
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
</div>
