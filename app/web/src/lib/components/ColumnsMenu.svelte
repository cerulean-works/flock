<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import Columns3Icon from '@lucide/svelte/icons/columns-3';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { tick } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import { toggle } from '#lib/utils.ts';
	import { type ColumnNode, columnTree, filterTree, folderState } from '#lib/workspace/columns.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	interface Props {
		/** Scroll the table to a column (shown by then). */
		onjump: (key: string) => void;
	}

	let { onjump }: Props = $props();

	const ws = getWorkspace();

	let open = $state(false);
	let query = $state('');
	/** Folders the viewer opened; the groups (Run, Config, Summary) start open. */
	const expanded = new SvelteSet<string>(['info', 'config', 'summary']);

	const tree = $derived(columnTree(ws.meta.columns));
	const searching = $derived(query.trim() !== '');
	const nodes = $derived(filterTree(tree, query));
	const hidden = $derived(ws.hiddenColumns);
	const total = $derived(ws.meta.columns.length);
	const hiddenCount = $derived(total - ws.shownColumns.length);

	/** The first column a search matches, for Enter. */
	function firstLeaf(list: readonly ColumnNode[]): string | null {
		for (const n of list) {
			const key = n.kind === 'leaf' ? n.key : firstLeaf(n.children);
			if (key) return key;
		}
		return null;
	}

	/** Show the column (if hidden), close, and scroll the table to it. */
	async function jump(key: string) {
		if (hidden.has(key)) ws.setColumnsHidden([key], false);
		open = false;
		await tick();
		onjump(key);
	}

	function onsearchkey(e: KeyboardEvent) {
		if (e.key !== 'Enter') return;
		e.preventDefault();
		const key = firstLeaf(nodes);
		if (key) void jump(key);
	}

	const ROW = 'flex h-7 items-center gap-1.5 rounded-md pr-1.5 hover:bg-muted';
	const indent = (depth: number) => `${4 + depth * 16}px`;
</script>

<!-- One level of the tree; folders open while searching so every match shows. -->
{#snippet level(list: readonly ColumnNode[], depth: number)}
	{#each list as n (n.kind === 'folder' ? `/${n.id}` : n.key)}
		{#if n.kind === 'folder'}
			{@const state = folderState(n.keys, hidden)}
			{@const isOpen = searching || expanded.has(n.id)}
			{@const shownHere = n.keys.filter((k) => !hidden.has(k)).length}
			<li>
				<div class={ROW} style:padding-left={indent(depth)}>
					<button
						class="grid size-5 flex-none place-items-center rounded text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
						aria-expanded={isOpen}
						aria-label="{isOpen ? 'Collapse' : 'Expand'} {n.name}"
						disabled={searching}
						onclick={() => toggle(expanded, n.id)}
					>
						<ChevronRightIcon class={['size-3.5 transition-transform', isOpen && 'rotate-90']} />
					</button>
					<Checkbox
						checked={state === 'all'}
						indeterminate={state === 'some'}
						disabled={!n.keys.length}
						onCheckedChange={() => ws.setColumnsHidden(n.keys, state === 'all')}
						aria-label="Show every column in {n.name}"
					/>
					<button
						class="min-w-0 flex-1 truncate rounded text-left font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-default"
						disabled={searching}
						onclick={() => toggle(expanded, n.id)}
					>
						{n.name}
					</button>
					<span class="flex-none text-xs text-faint tabular-nums">
						{shownHere}/{n.keys.length}
					</span>
				</div>
				{#if isOpen}
					<ul>{@render level(n.children, depth + 1)}</ul>
				{/if}
			</li>
		{:else}
			{@const shown = n.locked || !hidden.has(n.key)}
			<li class={ROW} style:padding-left={indent(depth)}>
				<span class="size-5 flex-none"></span>
				<Checkbox
					checked={shown}
					disabled={n.locked}
					onCheckedChange={(v) => ws.setColumnsHidden([n.key], !v)}
					aria-label={n.locked ? `${n.key} is always shown` : `Show ${n.key}`}
					title={n.locked ? 'Always shown' : undefined}
				/>
				<button
					class={[
						'min-w-0 flex-1 truncate rounded text-left outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50',
						!shown && 'text-muted-foreground'
					]}
					title="Jump to {n.key}"
					onclick={() => jump(n.key)}
				>
					{n.name}
				</button>
			</li>
		{/if}
	{/each}
{/snippet}

<Popover.Root bind:open>
	<Popover.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant={hiddenCount ? 'secondary' : 'outline'}
				title={hiddenCount
					? `${hiddenCount} of ${total} columns hidden`
					: 'Show, hide or jump to columns'}
			>
				<Columns3Icon /> Columns
				{#if hiddenCount}
					<span class="font-normal text-muted-foreground tabular-nums">{hiddenCount} hidden</span>
				{/if}
			</Button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content align="start" class="w-80 gap-0 p-0">
		<div class="relative border-b p-2">
			<SearchIcon
				class="pointer-events-none absolute top-1/2 left-4.5 size-3.5 -translate-y-1/2 text-muted-foreground"
			/>
			<Input
				bind:value={query}
				class="pl-8"
				placeholder="Find a column"
				aria-label="Find a column"
				onkeydown={onsearchkey}
			/>
		</div>
		<div class="max-h-[min(60vh,26rem)] overflow-y-auto p-1.5 text-[13px]">
			{#if nodes.length}
				<ul aria-label="Columns">{@render level(nodes, 0)}</ul>
			{:else}
				<p class="px-2 py-3 text-muted-foreground">No column’s key contains “{query.trim()}”.</p>
			{/if}
		</div>
		<div class="flex items-center gap-2 border-t py-1.5 pr-1.5 pl-3 text-xs text-muted-foreground">
			<span class="tabular-nums">{ws.shownColumns.length} of {total} shown</span>
			<span class="flex-1"></span>
			<Button variant="ghost" size="xs" disabled={!hiddenCount} onclick={() => ws.showAllColumns()}>
				Show all
			</Button>
		</div>
	</Popover.Content>
</Popover.Root>
