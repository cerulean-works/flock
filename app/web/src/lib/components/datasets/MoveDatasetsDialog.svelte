<script lang="ts">
	import CornerLeftUpIcon from '@lucide/svelte/icons/corner-left-up';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import {
		DATASETS_BUCKET,
		DatasetError,
		type DatasetItem,
		datasetCall,
		datasetsApi,
		itemName,
		nameOf,
		parentOf,
		pathError,
		within
	} from '#lib/datasets.ts';
	import type { DatasetFolder, MoveResult } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import FormError from '../FormError.svelte';
	import ItemFailures from '../ItemFailures.svelte';

	interface Props {
		open: boolean;
		org: string;
		/** The folder shown, where the destination starts. */
		from: string;
		items: readonly DatasetItem[];
		/** Once the request answered: the items that moved leave the list. */
		onmoved: (results: MoveResult[]) => void | Promise<void>;
	}

	let { open = $bindable(), org, from, items, onmoved }: Props = $props();

	// Reset once the dialog has closed, so its text stays put while it animates closed.
	let to = $derived(from);
	let error = $state<string | null>(null);
	let command = $state<string | null>(null);
	let failures = $state<MoveResult[]>([]);
	let pending = $state(false);

	function reset() {
		to = from;
		error = null;
		command = null;
		failures = [];
	}

	/** The destination as typed, without slashes at either end. */
	const target = $derived(to.trim().replace(/^\/+|\/+$/g, ''));

	// The folder picker lists the folders in the destination, read as it's typed.
	let browsing = $state<string | null>(null);
	let folders = $state<{ name: string; path: string }[] | null>(null);
	$effect(() => {
		if (!open) return;
		const at = target;
		if (at && pathError(at)) {
			folders = [];
			return;
		}
		const controller = new AbortController();
		const timer = setTimeout(() => void list(at, controller.signal), at === browsing ? 0 : 200);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	// Folders come after a page's datasets, so a few pages are read to find them.
	const PAGES = 5;
	async function list(at: string, signal: AbortSignal) {
		const found: { name: string; path: string }[] = [];
		let cursor: string | null = null;
		try {
			for (let n = 0; n < PAGES; n++) {
				const page: DatasetFolder = await datasetCall<DatasetFolder>(
					datasetsApi(org, 'at', { path: at, cursor, details: '0' }),
					{ signal }
				);
				if (page.kind !== 'folder') break;
				found.push(...page.folders);
				cursor = page.cursor;
				if (!cursor) break;
			}
		} catch (e) {
			if ((e as Error).name === 'AbortError') return;
		}
		browsing = at;
		// A folder can't move into itself.
		folders = found.filter((f) => !items.some((i) => i.folder && within(f.path, i.path)));
	}

	const name = $derived(items.length === 1 ? itemName(items[0]) : count(items.length, 'item'));

	async function submit() {
		const invalid = target ? pathError(target) : null;
		if (invalid) {
			error = invalid;
			return;
		}
		if (items.every((i) => parentOf(i.path) === target)) {
			error = items.length === 1 ? 'It’s already in this folder' : 'They’re already in this folder';
			return;
		}
		error = null;
		command = null;
		pending = true;
		try {
			const { results } = await datasetCall<{ results: MoveResult[] }>(datasetsApi(org, 'move'), {
				method: 'POST',
				body: { paths: items.map((i) => i.path), to: target }
			});
			const moved = results.filter((r) => r.moved).length;
			failures = results.filter((r) => !r.moved);
			await onmoved(results);
			if (moved)
				toast.success(
					`Moved ${moved === items.length ? name : count(moved, 'item')} to ${target || DATASETS_BUCKET}`
				);
			if (!failures.length) open = false;
		} catch (e) {
			error = (e as Error).message;
			command =
				e instanceof DatasetError && typeof e.body.command === 'string' ? e.body.command : null;
		} finally {
			pending = false;
		}
	}
</script>

<!-- While the items move, the dialog can't be closed: the request decides how it ends. -->
<Dialog.Root
	bind:open={() => open, (o) => (open = pending ? true : o)}
	onOpenChangeComplete={(o) => o || reset()}
>
	<Dialog.Content
		class="sm:max-w-lg"
		showCloseButton={!pending}
		escapeKeydownBehavior={pending ? 'ignore' : 'close'}
		interactOutsideBehavior={pending ? 'ignore' : 'close'}
	>
		<Dialog.Header>
			<Dialog.Title class="break-all">Move {name}</Dialog.Title>
			<Dialog.Description>To another folder in {DATASETS_BUCKET}.</Dialog.Description>
		</Dialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (!failures.length) void submit();
			}}
		>
			{#if failures.length}
				<ItemFailures {failures} summary="{count(failures.length, 'item')} didn’t move." />
			{:else}
				<div class="grid gap-2">
					<Label for="move-datasets-to">Folder</Label>
					<Input
						id="move-datasets-to"
						class="font-mono text-[13px]"
						bind:value={to}
						placeholder="The top of {DATASETS_BUCKET}"
						disabled={pending}
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
								disabled={pending}
								onclick={() => (to = parentOf(target))}
							>
								<CornerLeftUpIcon class="size-4 flex-none" />
								<span class="truncate"
									>{parentOf(target) ? nameOf(parentOf(target)) : DATASETS_BUCKET}</span
								>
							</button>
						{/if}
						{#each folders ?? [] as f (f.path)}
							<button
								type="button"
								class="flex h-8 w-full items-center gap-2 px-2.5 text-left hover:bg-muted"
								disabled={pending}
								onclick={() => (to = f.path)}
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
				</div>
			{/if}
			<FormError {error} />
			{#if command}<CopyBlock code={command} />{/if}
			<Dialog.Footer>
				{#if failures.length}
					<Button type="button" onclick={() => (open = false)}>Close</Button>
				{:else}
					<Button type="button" variant="outline" disabled={pending} onclick={() => (open = false)}>
						Cancel
					</Button>
					<Button type="submit" disabled={pending}>
						{#if pending}<LoaderCircleIcon class="animate-spin" /> Moving…{:else}Move{/if}
					</Button>
				{/if}
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
