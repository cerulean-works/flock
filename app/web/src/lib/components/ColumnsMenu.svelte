<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
	import LockIcon from '@lucide/svelte/icons/lock';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { tick } from 'svelte';
	import { flip } from 'svelte/animate';
	import { prefersReducedMotion } from 'svelte/motion';
	import { SvelteSet } from 'svelte/reactivity';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { toggle } from '#lib/utils.ts';
	import {
		columnLabel,
		type ColumnGroup,
		type ColumnNode,
		LOCKED_COLUMNS,
		columnTree,
		filterTree,
		findFolder,
		folderState
	} from '#lib/workspace/columns.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	interface Props {
		/** Scroll the table to a column (shown by then). */
		onjump: (key: string) => void;
		/**
		 * Only these columns, in this order (the Evals preset's suites); the project's others keep
		 * their settings. Every column when not set.
		 */
		keys?: readonly string[];
		/** Closes the view's settings (ViewSettings), which hold this list. */
		onclose?: () => void;
	}

	let { onjump, keys, onclose }: Props = $props();

	const ws = getWorkspace();
	let search = $state<HTMLInputElement | null>(null);

	/** The run's own columns by the names the table shows (State, Job type). */
	function leafName(key: string, name: string) {
		const col = ws.meta.columns.find((c) => c.key === key);
		return col ? columnLabel(col) : name;
	}

	let query = $state('');
	/** Folders the viewer opened; the groups (Run, Config, Summary) start open. */
	const expanded = new SvelteSet<string>(['info', 'config', 'summary']);

	const listed = $derived.by(() => {
		if (!keys) return ws.arrangedColumns.map((i) => ws.meta.columns[i]);
		const byKey = new Map(ws.meta.columns.map((c) => [c.key, c]));
		return keys.flatMap((k) => byKey.get(k) ?? []);
	});
	const tree = $derived(columnTree(listed));
	const searching = $derived(query.trim() !== '');
	const nodes = $derived(filterTree(tree, query));
	const hidden = $derived(ws.hiddenColumns);
	const total = $derived(listed.length);
	const hiddenCount = $derived(
		listed.filter((c) => hidden.has(c.key) && !LOCKED_COLUMNS.has(c.key)).length
	);
	const moved = $derived(
		keys ? ws.columnPrefs.order.some((k) => keys.includes(k)) : ws.columnsMoved
	);

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
		onclose?.();
		await tick();
		onjump(key);
	}

	function onsearchkey(e: KeyboardEvent) {
		if (e.key !== 'Enter') return;
		e.preventDefault();
		const key = firstLeaf(nodes);
		if (key) void jump(key);
	}

	// ------------------------------------------------------------------------------------------
	// Moving groups, folders and columns, each among its siblings: what's dragged follows the
	// pointer while the others make room for it (as panels do). Lists are named by their folder's
	// id; the groups' own list is ''. Moving waits for the search to be cleared, so every row a
	// list holds is in view.

	/** A node's id among its siblings, as its row is marked. */
	const idOf = (n: ColumnNode) => (n.kind === 'folder' ? `/${n.id}` : n.key);

	function siblings(parent: string): readonly ColumnNode[] {
		return parent ? (findFolder(tree, parent)?.children ?? []) : tree;
	}

	const indexIn = (parent: string, id: string) => siblings(parent).findIndex((n) => idOf(n) === id);

	function move(parent: string, from: number, to: number) {
		if (parent) ws.moveColumn(parent, from, to, keys);
		else ws.moveColumnGroup(tree[from].id as ColumnGroup, to);
	}

	/** The row being dragged, its list, and how far it is from its place there. */
	let drag = $state<{ parent: string; id: string; y: number } | null>(null);
	let scroller = $state<HTMLDivElement | null>(null);
	/** The last move, for screen readers. */
	let status = $state('');

	const EDGE = 40; // px from the list's top or bottom where dragging scrolls it

	/**
	 * Follow a drag from `handle`: `begin` once the pointer has moved a few px, then `follow` on
	 * every move and while the list scrolls under a pointer held near its edge; `end` on release.
	 */
	function track(
		e: PointerEvent,
		handle: HTMLElement,
		{ begin, follow, end }: { begin: () => void; follow: () => void; end: () => void }
	) {
		handle.setPointerCapture(e.pointerId);
		const start = { x: e.clientX, y: e.clientY };
		const pointer = { ...start };
		let started = false;
		let frame = 0;

		function scroll() {
			frame = 0;
			if (!started || !scroller) return;
			const r = scroller.getBoundingClientRect();
			const by =
				pointer.y < r.top + EDGE
					? -(r.top + EDGE - pointer.y) / 4
					: pointer.y > r.bottom - EDGE
						? (pointer.y - (r.bottom - EDGE)) / 4
						: 0;
			if (!by) return;
			scroller.scrollTop += by;
			follow();
			frame = requestAnimationFrame(scroll);
		}

		const moved = (ev: PointerEvent) => {
			pointer.x = ev.clientX;
			pointer.y = ev.clientY;
			if (!started) {
				if (Math.hypot(pointer.x - start.x, pointer.y - start.y) < 4) return;
				started = true;
				begin();
			}
			follow();
			if (!frame) frame = requestAnimationFrame(scroll);
		};
		const up = () => {
			cancelAnimationFrame(frame);
			started = false;
			end();
			handle.removeEventListener('pointermove', moved);
			handle.removeEventListener('pointerup', up);
			handle.removeEventListener('pointercancel', up);
		};
		handle.addEventListener('pointermove', moved);
		handle.addEventListener('pointerup', up);
		handle.addEventListener('pointercancel', up);
		return pointer;
	}

	/** A press on a row that starts a drag: the main button, off its controls (but its grip). */
	function grabs(e: PointerEvent) {
		const target = e.target as HTMLElement;
		return (
			e.button === 0 && !(target.closest('button, a, input') && !target.closest('[data-grip]'))
		);
	}

	function grab(e: PointerEvent, parent: string, id: string) {
		if (searching || !grabs(e)) return;
		e.preventDefault();
		const handle = e.currentTarget as HTMLElement;
		const item = handle.closest<HTMLElement>('[data-node]')!;
		const list = item.parentElement!;
		const offsetY = e.clientY - item.getBoundingClientRect().top;

		/**
		 * Follow the pointer, and move the row past every sibling whose middle the pointer is
		 * below. Open folders are taller than a row, so slots are halves, not the boxes.
		 */
		async function follow() {
			if (!drag) return;
			const top = list.getBoundingClientRect().top;
			drag.y = pointer.y - offsetY - (top + item.offsetTop);
			let to = 0;
			for (const other of list.children as HTMLCollectionOf<HTMLElement>) {
				if (other !== item && pointer.y > top + other.offsetTop + other.offsetHeight / 2) to++;
			}
			const from = indexIn(parent, id);
			if (from === to) return;
			rearrange();
			move(parent, from, to);
			await tick();
			// Locked columns hold their places: only follow on once it moved.
			if (indexIn(parent, id) !== from) void follow();
		}

		const pointer = track(e, handle, {
			begin: () => (drag = { parent, id, y: 0 }),
			follow: () => void follow(),
			end: () => (drag = null)
		});
	}

	/** Keyboard moves, keeping focus on the row's grip as it changes place. */
	async function nudge(e: KeyboardEvent, parent: string, n: ColumnNode) {
		const by = ({ ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 } as const)[
			e.key as 'ArrowLeft'
		];
		if (by == null) return;
		e.preventDefault();
		const id = idOf(n);
		const list = siblings(parent);
		const from = indexIn(parent, id);
		if (from + by < 0 || from + by >= list.length) return;
		rearrange();
		move(parent, from, from + by);
		await tick();
		const at = indexIn(parent, id);
		if (at !== from) status = `${n.name} moved to ${at + 1} of ${list.length}`;
		scroller?.querySelector<HTMLElement>(`[data-node="${CSS.escape(id)}"] [data-grip]`)?.focus();
	}

	const settle = $derived(prefersReducedMotion.current ? 0 : 160);

	/** Set for a moment after the viewer moves a row, so only their moves animate. */
	let rearranging = $state(false);
	let rearrangeTimer: ReturnType<typeof setTimeout> | undefined;
	function rearrange() {
		rearranging = true;
		clearTimeout(rearrangeTimer);
		rearrangeTimer = setTimeout(() => (rearranging = false), settle + 50);
	}
	$effect(() => () => clearTimeout(rearrangeTimer));

	const ROW = 'flex h-8 items-center gap-1.5 rounded-md pr-2';
	const ICON_BTN =
		'grid size-5 flex-none place-items-center rounded text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50';
	const indent = (depth: number) => `${4 + depth * 16}px`;
</script>

<!-- The handle of a movable row, or what holds its place. -->
{#snippet grip(parent: string, n: ColumnNode, label: string)}
	{#if n.kind === 'leaf' && n.locked}
		<span
			class="grid size-5 flex-none place-items-center text-faint"
			{@attach hint('Always shown, always first')}
		>
			<LockIcon class="size-3" />
		</span>
	{:else if searching}
		<span class="size-5 flex-none"></span>
	{:else}
		<button
			class="grid size-5 flex-none cursor-[inherit] touch-none place-items-center rounded text-faint outline-none hover:text-muted-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
			data-grip
			aria-label="Move {label} (arrow keys)"
			{@attach hint('Drag to move')}
			onkeydown={(e) => nudge(e, parent, n)}
		>
			<GripVerticalIcon class="size-3.5" />
		</button>
	{/if}
{/snippet}

<!-- One list of siblings; folders open while searching so every match shows. -->
{#snippet level(list: readonly ColumnNode[], parent: string, depth: number)}
	{#each list as n (idOf(n))}
		{@const id = idOf(n)}
		{@const moving = drag?.parent === parent && drag.id === id}
		<li
			class={[
				'rounded-md',
				!parent && 'mt-1 first:mt-0',
				moving
					? 'relative z-2 bg-popover shadow-lg ring-1 ring-foreground/15'
					: 'transition-transform'
			]}
			style:transform={moving ? `translateY(${drag!.y}px)` : null}
			style:transition-duration={moving ? null : `${settle}ms`}
			data-node={id}
			animate:flip={{ duration: moving || !rearranging ? 0 : settle }}
		>
			{#if n.kind === 'folder'}
				{@const state = folderState(n.keys, hidden)}
				{@const isOpen = searching || expanded.has(n.id)}
				{@const shownHere = n.keys.filter((k) => !hidden.has(k)).length}
				<!-- The row is the handle; its controls keep their clicks. -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div
					class={[
						ROW,
						!searching && (moving ? 'cursor-grabbing' : 'cursor-grab'),
						!moving && 'hover:bg-muted'
					]}
					style:padding-left={indent(depth)}
					onpointerdown={(e) => grab(e, parent, id)}
				>
					{@render grip(parent, n, parent ? `folder ${n.name}` : `the ${n.name} group`)}
					<button
						class={ICON_BTN}
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
				{#if isOpen && !moving}
					<ul class="relative">{@render level(n.children, n.id, depth + 1)}</ul>
				{/if}
			{:else}
				{@const shown = n.locked || !hidden.has(n.key)}
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<div
					class={[
						ROW,
						!searching && !n.locked && (moving ? 'cursor-grabbing' : 'cursor-grab'),
						!moving && 'hover:bg-muted'
					]}
					style:padding-left={indent(depth)}
					onpointerdown={(e) => !n.locked && grab(e, parent, id)}
				>
					{@render grip(parent, n, n.key)}
					<span class="size-5 flex-none"></span>
					<Checkbox
						checked={shown}
						disabled={n.locked}
						onCheckedChange={(v) => ws.setColumnsHidden([n.key], !v)}
						aria-label={n.locked ? `${n.key} is always shown` : `Show ${n.key}`}
						{@attach hint(n.locked ? 'Always shown' : undefined)}
					/>
					<button
						class={[
							'min-w-0 flex-1 truncate rounded text-left outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50',
							!shown && 'text-muted-foreground'
						]}
						{@attach hint(`Jump to ${n.key}`)}
						onclick={() => jump(n.key)}
					>
						{leafName(n.key, n.name)}
					</button>
				</div>
			{/if}
		</li>
	{/each}
{/snippet}

{#snippet body()}
	<div class="relative m-2">
		<SearchIcon
			class="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
		/>
		<Input
			bind:ref={search}
			bind:value={query}
			class="h-8 pl-8"
			placeholder={searching ? 'Clear the search to reorder' : 'Find a column'}
			aria-label="Find a column"
			onkeydown={onsearchkey}
		/>
	</div>
	<div class="min-h-0 flex-1 overflow-y-auto border-t p-2 text-[13px]" bind:this={scroller}>
		{#if nodes.length}
			<ul class="relative" aria-label="Columns">{@render level(nodes, '', 0)}</ul>
		{:else}
			<p class="px-2 py-3 text-muted-foreground">No column’s key contains “{query.trim()}”.</p>
		{/if}
	</div>
	<p class="sr-only" aria-live="polite">{status}</p>
	<div class="flex flex-wrap items-center gap-1 border-t py-1.5 pr-1.5 pl-3">
		<span class="mr-auto text-xs text-muted-foreground tabular-nums">
			{total - hiddenCount} of {total} shown
		</span>
		<Button
			variant="quiet"
			size="bar"
			disabled={!moved}
			onclick={() => {
				ws.resetColumnOrder(keys);
				status = 'Columns back in their default order';
			}}
		>
			Reset order
		</Button>
		<Button
			variant="quiet"
			size="bar"
			disabled={!hiddenCount}
			onclick={() => (keys ? ws.setColumnsHidden(keys, false) : ws.showAllColumns())}
		>
			Show all
		</Button>
	</div>
{/snippet}

<div
	class="flex max-h-[min(70vh,36rem)] min-h-0 flex-col"
	{@attach (el) => el.querySelector('input')?.focus()}
>
	{@render body()}
</div>
