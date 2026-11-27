<script lang="ts">
	import BlendIcon from '@lucide/svelte/icons/blend';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import GitForkIcon from '@lucide/svelte/icons/git-fork';
	import FolderInputIcon from '@lucide/svelte/icons/folder-input';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';

	/**
	 * A data list row's menu items (D133), for its ⋯ and a right-click alike: the row's own, or,
	 * when it is one of several checked, the selection's (as the runs table's menu does).
	 */
	interface Props {
		/** What the menu acts on when it's several rows: "3 datasets". */
		many?: string | null;
		/** A dataset's row also forks it (023); one row only. */
		onfork?: () => void;
		/** One row only. */
		onrename?: () => void;
		/** Datasets blend from their selection. */
		onblend?: () => void;
		/** A stored file downloads; one row only. */
		ondownload?: () => void;
		/** Lists whose rows move from their menu (models, D166) say so. */
		onmove?: () => void;
		/** Left out where nothing can be deleted (a read-only bucket). */
		ondelete?: () => void;
	}

	let { many = null, onfork, onrename, onblend, ondownload, onmove, ondelete }: Props = $props();
	const before = $derived(!!(many ? onblend : onfork || onrename || ondownload) || !!onmove);
</script>

{#if many}
	<DropdownMenu.Label class="text-xs font-normal text-muted-foreground">{many}</DropdownMenu.Label>
	{#if onblend}
		<DropdownMenu.Item onSelect={onblend}><BlendIcon /> Blend…</DropdownMenu.Item>
	{/if}
{:else}
	{#if ondownload}
		<DropdownMenu.Item onSelect={ondownload}><DownloadIcon /> Download</DropdownMenu.Item>
	{/if}
	{#if onfork}
		<DropdownMenu.Item onSelect={onfork}><GitForkIcon /> Fork…</DropdownMenu.Item>
	{/if}
	{#if onrename}
		<DropdownMenu.Item onSelect={onrename}><PencilIcon /> Rename…</DropdownMenu.Item>
	{/if}
{/if}
{#if onmove}
	<DropdownMenu.Item onSelect={onmove}><FolderInputIcon /> Move…</DropdownMenu.Item>
{/if}
{#if ondelete}
	{#if before}<DropdownMenu.Separator />{/if}
	<DropdownMenu.Item variant="destructive" onSelect={ondelete}>
		<Trash2Icon />
		{many ? `Delete ${many}…` : 'Delete…'}
	</DropdownMenu.Item>
{/if}
