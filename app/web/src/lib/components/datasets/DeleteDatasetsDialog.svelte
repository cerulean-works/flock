<script lang="ts">
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import { toast } from 'svelte-sonner';
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import {
		DatasetError,
		TRASH_DAYS,
		type DatasetItem,
		datasetCall,
		datasetsApi,
		itemName,
		itemPath
	} from '#lib/datasets.ts';
	import type { DeleteResult } from '#lib/types.ts';
	import { count } from '#lib/utils.ts';
	import ConfirmField from '../ConfirmField.svelte';
	import CopyBlock from '../CopyBlock.svelte';
	import FormError from '../FormError.svelte';
	import ItemFailures from '../ItemFailures.svelte';

	interface Props {
		open: boolean;
		org: string;
		items: readonly DatasetItem[];
		/** Once the request answered: the items deleted leave the list. */
		ondeleted: (results: DeleteResult[]) => void | Promise<void>;
	}

	let { open = $bindable(), org, items, ondeleted }: Props = $props();

	/** The names listed; the rest are counted. */
	const LISTED = 10;

	// One item is confirmed by its path, several by how many there are (D132). Reset once the
	// dialog has closed, so its text stays put while it animates closed.
	const expected = $derived(items.length === 1 ? items[0].path : String(items.length));
	let typed = $state('');
	let error = $state<string | null>(null);
	let command = $state<string | null>(null);
	let failures = $state<DeleteResult[]>([]);
	let pending = $state(false);

	function reset() {
		typed = '';
		error = null;
		command = null;
		failures = [];
	}

	const name = $derived(items.length === 1 ? itemName(items[0]) : count(items.length, 'item'));

	async function remove() {
		pending = true;
		error = null;
		command = null;
		try {
			const { results } = await datasetCall<{ results: DeleteResult[] }>(
				datasetsApi(org, 'delete'),
				{ method: 'POST', body: { paths: items.map((i) => i.path) } }
			);
			const deleted = results.filter((r) => r.deleted).length;
			failures = results.filter((r) => !r.deleted);
			await ondeleted(results);
			if (deleted)
				toast.success(`Deleted ${deleted === items.length ? name : count(deleted, 'item')}`);
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

<AlertDialog.Root
	bind:open={() => open, (o) => (open = pending ? true : o)}
	onOpenChangeComplete={(o) => o || reset()}
>
	<AlertDialog.Content class="sm:max-w-md">
		<AlertDialog.Header>
			<AlertDialog.Title class="break-all">Delete {name}?</AlertDialog.Title>
			<AlertDialog.Description>
				{#if items.some((i) => i.folder)}
					Folders are deleted with everything in them.
				{/if}
				Org admins can restore {items.length === 1 ? 'it' : 'them'} for {TRASH_DAYS} days.
			</AlertDialog.Description>
		</AlertDialog.Header>
		<form
			class="grid gap-4"
			onsubmit={(e) => {
				e.preventDefault();
				if (typed === expected && !failures.length) void remove();
			}}
		>
			{#if failures.length}
				<ItemFailures
					{failures}
					summary="{count(failures.length, 'item')} {failures.length === 1
						? 'wasn’t'
						: 'weren’t'} deleted."
				/>
			{:else}
				{#if items.length > 1}
					<ul class="max-h-48 overflow-y-auto border-y border-divider py-1 text-[13px]">
						{#each items.slice(0, LISTED) as item (item.path)}
							<li class="flex items-center gap-2 py-0.5">
								{#if item.folder}
									<FolderIcon class="size-4 flex-none text-muted-foreground" />
								{:else}
									<DatabaseIcon class="size-4 flex-none text-muted-foreground" />
								{/if}
								<span class="truncate font-mono text-[12.5px]">{itemPath(item)}</span>
							</li>
						{/each}
						{#if items.length > LISTED}
							<li class="py-0.5 text-muted-foreground">
								and {(items.length - LISTED).toLocaleString('en-US')} more
							</li>
						{/if}
					</ul>
				{/if}
				<ConfirmField
					id="delete-datasets-confirm"
					text={expected}
					bind:value={typed}
					disabled={pending}
				/>
			{/if}
			<FormError {error} />
			{#if command}<CopyBlock code={command} />{/if}
			<AlertDialog.Footer>
				{#if failures.length}
					<Button type="button" onclick={() => (open = false)}>Close</Button>
				{:else}
					<AlertDialog.Cancel disabled={pending}>Cancel</AlertDialog.Cancel>
					<Button type="submit" variant="destructive" disabled={typed !== expected || pending}>
						{pending
							? 'Deleting…'
							: `Delete ${items.length === 1 ? (items[0].folder ? 'folder' : 'dataset') : count(items.length, 'item')}`}
					</Button>
				{/if}
			</AlertDialog.Footer>
		</form>
	</AlertDialog.Content>
</AlertDialog.Root>
