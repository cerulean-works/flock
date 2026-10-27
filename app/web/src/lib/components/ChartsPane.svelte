<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
	import { tick } from 'svelte';
	import { flip } from 'svelte/animate';
	import { prefersReducedMotion } from 'svelte/motion';
	import type { ResolvedPathname } from '$app/types';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { pageAt, ROWS_MAX, ROWS_MIN } from '#lib/workspace/layout.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import PanelCard from './PanelCard.svelte';
	import { BAND_H, HEAD_H } from './RunsTable.svelte';

	interface Props {
		shown: boolean;
		/** Plots are live (shown and not covered by a full-screen panel). */
		active: boolean;
		panelHref: (panel: number) => ResolvedPathname;
		onfullscreen: (panel: number, e: MouseEvent) => void;
		/**
		 * Beside the runs table (the workspace), whose header the first section's lines up with;
		 * otherwise the pane fills its container (a run's page).
		 */
		beside?: boolean;
	}

	let { shown, active, panelHref, onfullscreen, beside = true }: Props = $props();

	const ws = getWorkspace();
	let root = $state<HTMLDivElement | null>(null);

	/**
	 * Columns of panels the grid fits: every section's grid is as wide as the list, so one is
	 * measured for all. Its tracks are the template's, resolved; four until the grid is laid out.
	 */
	let columns = $state(4);

	function observeColumns(el: HTMLElement) {
		const measure = () => {
			for (const grid of el.querySelectorAll<HTMLElement>('[data-grid]')) {
				const tracks = getComputedStyle(grid).gridTemplateColumns;
				// A hidden grid's template is "none": a folded section's.
				if (!grid.offsetWidth || tracks === 'none') continue;
				columns = Math.max(1, tracks.split(' ').length);
				return;
			}
		};
		const observer = new ResizeObserver(measure);
		observer.observe(el);
		return () => observer.disconnect();
	}

	/** Panels to a page of a section: as many rows as it shows, of as many as the grid fits. */
	const perPage = (key: string) => columns * ws.sectionRows(key);

	// ------------------------------------------------------------------------------------------
	// Moving panels and sections. What's dragged follows the pointer while the others make room
	// for it; it lands where it is dropped. The order is then kept for this project.

	/** The panel being dragged, and how far it is from its place in the grid. */
	let drag = $state<{ panel: number; x: number; y: number } | null>(null);
	/** The section being dragged, and how far it is from its place in the list. */
	let sectionDrag = $state<{ key: string; y: number } | null>(null);
	/** The section whose rows are being resized. */
	let rowsDrag = $state<string | null>(null);
	let list = $state<HTMLDivElement | null>(null);

	const EDGE = 56; // px from the pane's top or bottom where dragging scrolls it

	/**
	 * Follow a drag that a press started. Once the pointer has moved a few px, `begin`; then
	 * `follow` on every move, and while the pane scrolls under a pointer held near its top or
	 * bottom; `end` once, when it's let go. Escape, the window losing focus or the pointer being
	 * taken away `cancel` first. `pointer` is kept current, so `follow` can run again later.
	 *
	 * The pane captures the pointer, not the handle: handles move with what they move, and
	 * moving the element that holds a capture releases it. Moves would then reach the charts
	 * under the pointer, and its release could be missed, leaving the drag hanging. Plots ignore
	 * the pointer while `ws.dragging`.
	 */
	function track(
		e: PointerEvent,
		{
			begin,
			follow,
			end,
			cancel
		}: { begin: () => void; follow: () => void; end: () => void; cancel?: () => void }
	) {
		const pane = root!;
		const id = e.pointerId;
		const start = { x: e.clientX, y: e.clientY };
		const pointer = { ...start };
		let started = false;
		let done = false;
		let frame = 0;

		function scroll() {
			frame = 0;
			if (done) return;
			const r = pane.getBoundingClientRect();
			const by =
				pointer.y < r.top + EDGE
					? -(r.top + EDGE - pointer.y) / 4
					: pointer.y > r.bottom - EDGE
						? (pointer.y - (r.bottom - EDGE)) / 4
						: 0;
			if (!by) return;
			pane.scrollTop += by;
			follow();
			frame = requestAnimationFrame(scroll);
		}

		function finish(cancelled: boolean) {
			if (done) return;
			done = true;
			cancelAnimationFrame(frame);
			window.removeEventListener('pointermove', move, true);
			window.removeEventListener('pointerup', up, true);
			window.removeEventListener('pointercancel', stop, true);
			window.removeEventListener('keydown', key, true);
			window.removeEventListener('blur', stop);
			pane.removeEventListener('lostpointercapture', lost);
			if (!started) return;
			if (pane.hasPointerCapture(id)) pane.releasePointerCapture(id);
			ws.dragging = false;
			if (cancelled) cancel?.();
			end();
		}

		const move = (ev: PointerEvent) => {
			if (ev.pointerId !== id) return;
			pointer.x = ev.clientX;
			pointer.y = ev.clientY;
			if (!started) {
				if (Math.hypot(pointer.x - start.x, pointer.y - start.y) < 4) return;
				started = true;
				ws.cursor = null;
				ws.hover = null;
				ws.dragging = true;
				try {
					pane.setPointerCapture(id);
				} catch {
					// The pointer is gone already; its release still reaches the window.
				}
				begin();
			}
			follow();
			if (!frame) frame = requestAnimationFrame(scroll);
		};
		const up = (ev: PointerEvent) => ev.pointerId === id && finish(false);
		const stop = () => finish(true);
		const lost = (ev: PointerEvent) => ev.pointerId === id && finish(true);
		const key = (ev: KeyboardEvent) => {
			if (ev.key !== 'Escape') return;
			// Puts it back, and nothing else: the page's own Escape (clear the selection) waits.
			if (started) ev.stopPropagation();
			finish(true);
		};
		window.addEventListener('pointermove', move, true);
		window.addEventListener('pointerup', up, true);
		window.addEventListener('pointercancel', stop, true);
		window.addEventListener('keydown', key, true);
		window.addEventListener('blur', stop);
		pane.addEventListener('lostpointercapture', lost);
		return pointer;
	}

	/** A press on a handle that starts a drag: the main button, off its buttons (but its grip). */
	function grabs(e: PointerEvent) {
		const target = e.target as HTMLElement;
		return (
			e.button === 0 &&
			!(target.closest('button, a, [role="menu"]') && !target.closest('[data-grip]'))
		);
	}

	function grab(e: PointerEvent, section: string, panel: number) {
		if (!grabs(e) || !root) return;
		e.preventDefault();
		const card = (e.currentTarget as HTMLElement).closest<HTMLElement>('[data-panel]')!;
		const grid = card.parentElement!;
		const box = card.getBoundingClientRect();
		const offset = { x: e.clientX - box.left, y: e.clientY - box.top };
		const from = ws.sections.find((s) => s.key === section)!.panels.indexOf(panel);

		/**
		 * Follow the pointer, and move the panel to the slot under it: straight there, however
		 * far the pointer went since the last move.
		 */
		function follow() {
			if (!drag) return;
			const g = grid.getBoundingClientRect();
			drag.x = pointer.x - offset.x - (g.left + card.offsetLeft);
			drag.y = pointer.y - offset.y - (g.top + card.offsetTop);
			// Slots are where panels are laid out, not where an animation shows them.
			for (const el of grid.children as HTMLCollectionOf<HTMLElement>) {
				if (el === card) continue;
				const x = pointer.x - g.left - el.offsetLeft;
				const y = pointer.y - g.top - el.offsetTop;
				if (x >= 0 && y >= 0 && x < el.offsetWidth && y < el.offsetHeight) {
					const to = ws.sections
						.find((s) => s.key === section)!
						.panels.indexOf(Number(el.dataset.panel));
					rearrange();
					ws.movePanel(section, panel, to);
					void tick().then(follow);
					return;
				}
			}
		}

		const pointer = track(e, {
			begin: () => (drag = { panel, x: 0, y: 0 }),
			follow,
			end: () => (drag = null),
			cancel: () => (rearrange(), ws.movePanel(section, panel, from))
		});
	}

	/** Keyboard moves, keeping focus on the panel's grip as it changes place. */
	async function nudge(section: string, panel: number, by: -1 | 1) {
		const panels = ws.sections.find((s) => s.key === section)!.panels;
		const i = panels.indexOf(panel);
		if (i + by < 0 || i + by >= panels.length) return;
		// A panel moved past its page's edge goes to the next (or previous) page with it.
		const per = perPage(section);
		if (Math.floor(i / per) !== Math.floor((i + by) / per))
			ws.setSectionStart(section, Math.floor((i + by) / per) * per);
		rearrange();
		ws.movePanel(section, panel, i + by);
		await tick();
		root?.querySelector<HTMLElement>(`[data-panel="${panel}"] [data-grip]`)?.focus();
	}

	function grabSection(e: PointerEvent, key: string) {
		if (!grabs(e) || !list || !root) return;
		e.preventDefault();
		const el = (e.currentTarget as HTMLElement).closest<HTMLElement>('[data-section]')!;
		const box = el.getBoundingClientRect();
		const offsetY = e.clientY - box.top;
		const sections = list;
		const from = ws.sections.findIndex((s) => s.key === key);

		/**
		 * Follow the pointer, and move the section past every other whose middle the pointer is
		 * below: straight to its slot, however far the pointer went. Sections differ in height,
		 * so slots are halves, not the boxes themselves.
		 */
		function follow() {
			if (!sectionDrag) return;
			const top = sections.getBoundingClientRect().top;
			sectionDrag.y = pointer.y - offsetY - (top + el.offsetTop);
			let to = 0;
			for (const other of sections.children as HTMLCollectionOf<HTMLElement>) {
				if (other !== el && pointer.y > top + other.offsetTop + other.offsetHeight / 2) to++;
			}
			if (ws.sections.findIndex((s) => s.key === key) === to) return;
			rearrange();
			ws.moveSection(key, to);
			void tick().then(follow);
		}

		const pointer = track(e, {
			begin: () => (sectionDrag = { key, y: 0 }),
			follow,
			end: () => (sectionDrag = null),
			cancel: () => (rearrange(), ws.moveSection(key, from))
		});
	}

	/** Keyboard moves for a section, keeping focus on its grip. */
	async function nudgeSection(e: KeyboardEvent, key: string) {
		const by = ({ ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 } as const)[
			e.key as 'ArrowLeft'
		];
		if (by == null) return;
		e.preventDefault();
		const i = ws.sections.findIndex((s) => s.key === key);
		if (i + by < 0 || i + by >= ws.sections.length) return;
		rearrange();
		ws.moveSection(key, i + by);
		await tick();
		list?.querySelector<HTMLElement>(`[data-section="${CSS.escape(key)}"] [data-grip]`)?.focus();
	}

	// ------------------------------------------------------------------------------------------
	// Rows per section. A section's bottom edge drags in whole rows of panels; its page holds
	// that many rows of as many panels as fit across.

	/** Rows a section can show: enough for all of its panels, within bounds. */
	const rowsFor = (count: number) =>
		Math.max(ROWS_MIN, Math.min(ROWS_MAX, Math.ceil(count / columns)));

	/** Rows a section shows: as many as asked for, or enough for all of its panels. */
	const rowsShown = (key: string, count: number) => Math.min(ws.sectionRows(key), rowsFor(count));

	function resizeRows(e: PointerEvent, key: string, count: number) {
		if (e.button !== 0 || !root) return;
		e.preventDefault();
		const pane = root;
		const grid = (e.currentTarget as HTMLElement)
			.closest('[data-section]')!
			.querySelector<HTMLElement>('[data-grid]')!;
		const card = grid.firstElementChild as HTMLElement | null;
		// A row is a panel and the gap under it; panels of a section share one height.
		const rowH = (card?.offsetHeight ?? 0) + parseFloat(getComputedStyle(grid).rowGap || '0');
		if (!rowH) return;
		const saved = ws.layout.rows[key] ?? null;
		const rows0 = rowsShown(key, count);
		const max = rowsFor(count);
		const y0 = e.clientY;
		const scroll0 = pane.scrollTop;

		/** Rows nearest the pointer: whole ones, so the edge snaps from one to the next. */
		function follow() {
			const dy = pointer.y - y0 + pane.scrollTop - scroll0;
			const rows = Math.max(ROWS_MIN, Math.min(max, rows0 + Math.round(dy / rowH)));
			if (rows !== ws.sectionRows(key)) ws.setSectionRows(key, rows, false);
		}

		let cancelled = false;
		const pointer = track(e, {
			begin: () => (rowsDrag = key),
			follow,
			// Saved once, as it was left (or as it was, if it was put back).
			end: () => {
				rowsDrag = null;
				ws.setSectionRows(key, cancelled ? saved : (ws.layout.rows[key] ?? null));
			},
			cancel: () => (cancelled = true)
		});
	}

	function rowsKey(e: KeyboardEvent, key: string, count: number) {
		const by = { ArrowUp: -1, ArrowDown: 1 }[e.key];
		if (by == null) return;
		e.preventDefault();
		const rows = rowsShown(key, count) + by;
		if (rows >= ROWS_MIN && rows <= rowsFor(count)) ws.setSectionRows(key, rows);
	}

	const settle = $derived(prefersReducedMotion.current ? 0 : 200);

	/**
	 * Set for a moment after the viewer moves a panel or section, so only their moves animate.
	 * Anything else that shifts the grid (the page mounting, the saved order loading, the pane
	 * changing width) lands at once: a flip there plays as panels growing out of their old boxes.
	 */
	let rearranging = $state(false);
	let rearrangeTimer: ReturnType<typeof setTimeout> | undefined;
	function rearrange() {
		rearranging = true;
		clearTimeout(rearrangeTimer);
		rearrangeTimer = setTimeout(() => (rearranging = false), settle + 50);
	}
	$effect(() => () => clearTimeout(rearrangeTimer));
</script>

<!-- Scrolling moves panels under a still pointer; drop the stale crosshair rather than lie.
     Visibility transitions with opacity, so the pane stays laid out (and its charts keep
     painting in idle time) while hidden behind the table. While something is dragged the pane
     holds the pointer and nothing in it reacts to hover. -->
<div
	class={[
		'pane absolute inset-y-0 right-0 z-4 overflow-y-auto overscroll-contain bg-background px-5 pb-10',
		beside ? 'left-(--name-w) shadow-[-1px_0_0_var(--divider)]' : 'left-0',
		'transition-[opacity,translate,visibility] duration-150 ease-out',
		shown ? 'visible translate-x-0 opacity-100' : 'invisible translate-x-3 opacity-0',
		ws.dragging && (rowsDrag ? 'cursor-row-resize' : 'cursor-grabbing')
	]}
	style:padding-top="{beside ? BAND_H : 0}px"
	style:--section-h="{HEAD_H}px"
	bind:this={root}
	inert={!shown}
	onscroll={() => {
		if (ws.cursor) ws.cursor = null;
	}}
>
	<div
		class={['relative', ws.dragging && 'pointer-events-none']}
		bind:this={list}
		{@attach observeColumns}
	>
		{#each ws.sections as sec (sec.key)}
			{@const count = sec.panels.length}
			{@const per = perPage(sec.key)}
			{@const page = pageAt(ws.sectionStart[sec.key] ?? 0, per, count)}
			{@const pages = Math.ceil(count / per)}
			{@const from = page * per}
			{@const slice = sec.panels.slice(from, from + per)}
			{@const open = !ws.collapsed.has(sec.key)}
			{@const moving = sectionDrag?.key === sec.key}
			{@const rows = rowsShown(sec.key, count)}
			{@const resizing = rowsDrag === sec.key}
			<!-- A section being dragged folds to its header (its panels stay mounted), so the others
			     are within reach. -->
			<section
				class={['relative mt-2 first:mt-0', moving ? 'z-3' : 'transition-transform']}
				style:transform={moving ? `translateY(${sectionDrag!.y}px)` : null}
				style:transition-duration={moving ? null : `${settle}ms`}
				data-section={sec.key}
				animate:flip={{ duration: moving || !rearranging ? 0 : settle }}
			>
				<!-- Edge to edge, so the first one continues the runs table's header line. The
				     header is the section's handle; its buttons keep their clicks. -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<header
					class={[
						'group/section relative -mx-5 flex h-(--section-h) items-center gap-1 border-b bg-background px-5',
						moving ? 'cursor-grabbing shadow-xl ring-1 ring-foreground/20' : 'cursor-grab'
					]}
					onpointerdown={(e) => grabSection(e, sec.key)}
				>
					<button
						class={[
							'absolute top-1/2 left-0.5 grid h-6 w-4 -translate-y-1/2 cursor-[inherit] place-items-center rounded text-faint transition-opacity outline-none hover:text-muted-foreground focus-visible:opacity-100 focus-visible:ring-3 focus-visible:ring-ring/50',
							moving ? 'opacity-100' : 'opacity-0 group-hover/section:opacity-100'
						]}
						data-grip
						aria-label="Move section {sec.key} (arrow keys)"
						title="Drag to move"
						onkeydown={(e) => nudgeSection(e, sec.key)}
					>
						<GripVerticalIcon class="size-3.5" />
					</button>
					<Button
						variant="ghost"
						class="mr-auto -ml-2.5 h-8 gap-2 px-2.5 aria-expanded:bg-transparent hover:aria-expanded:bg-muted"
						onclick={() => ws.toggleSection(sec.key)}
						aria-expanded={open}
					>
						<ChevronDownIcon
							class={['text-muted-foreground transition-transform', !open && '-rotate-90']}
						/>
						<h2 class="text-[22px] font-normal tracking-[-0.01em]">{sec.key}</h2>
						<Badge variant="secondary" class="text-muted-foreground">{count}</Badge>
					</Button>
					{#if open && pages > 1}
						<span class="mr-1 text-[13px] text-muted-foreground tabular-nums">
							{from + 1}–{from + slice.length} of {count}
						</span>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label="Previous panels"
							disabled={page === 0}
							onclick={() => ws.setSectionStart(sec.key, (page - 1) * per)}
						>
							<ChevronLeftIcon />
						</Button>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label="Next panels"
							disabled={page >= pages - 1}
							onclick={() => ws.setSectionStart(sec.key, (page + 1) * per)}
						>
							<ChevronRightIcon />
						</Button>
					{/if}
				</header>
				{#if open}
					<div
						class={[
							'relative grid grid-cols-[repeat(auto-fill,minmax(min(100%,max(340px,calc((100%_-_2.25rem)/4))),1fr))] gap-3 pt-3 pb-2',
							moving && 'hidden'
						]}
						data-grid
					>
						{#each slice as p (ws.meta.panels[p].id)}
							{@const dragging = drag?.panel === p}
							<div
								class={['min-w-0', dragging ? 'relative z-2' : 'transition-transform']}
								style:transform={dragging ? `translate(${drag!.x}px, ${drag!.y}px)` : null}
								style:transition-duration={dragging ? null : `${settle}ms`}
								data-panel={p}
								animate:flip={{ duration: dragging || !rearranging ? 0 : settle }}
							>
								<PanelCard
									panel={p}
									{active}
									{root}
									{dragging}
									fullscreenHref={panelHref(p)}
									onfullscreen={(e) => onfullscreen(p, e)}
									ongrab={(e) => grab(e, sec.key, p)}
									onnudge={(by) => nudge(sec.key, p, by)}
								/>
							</div>
						{/each}
					</div>
					{#if !moving && count > columns}
						<!-- The section's bottom edge: rows of panels a page shows. A rule across the
						     section between it and the next, where a panel's own (shorter) height grip
						     isn't; double-click goes back to the default. A focusable separator is
						     ARIA's window splitter: arrow keys add and remove rows too. -->
						<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
						<div
							class={[
								'group/rows absolute inset-x-0 -bottom-1.5 z-1 grid h-3 cursor-row-resize touch-none place-items-center outline-none',
								'before:absolute before:inset-x-0 before:top-1/2 before:h-px before:transition-colors',
								resizing
									? 'before:bg-ring'
									: 'before:bg-transparent hover:before:bg-border focus-visible:before:bg-ring'
							]}
							role="separator"
							aria-orientation="horizontal"
							aria-label="Rows of {sec.key} panels"
							aria-valuenow={rows}
							aria-valuetext="{rows} {rows === 1 ? 'row' : 'rows'}"
							aria-valuemin={ROWS_MIN}
							aria-valuemax={rowsFor(count)}
							tabindex="0"
							title="Drag to show more or fewer rows · double-click to reset"
							onpointerdown={(e) => resizeRows(e, sec.key, count)}
							ondblclick={() => ws.setSectionRows(sec.key, null)}
							onkeydown={(e) => rowsKey(e, sec.key, count)}
						>
							<span
								class={[
									'relative rounded-full border bg-background px-2 text-[11px] leading-4 text-muted-foreground tabular-nums transition-opacity',
									resizing
										? 'border-ring text-foreground opacity-100'
										: 'opacity-0 group-hover/rows:opacity-100 group-focus-visible/rows:opacity-100'
								]}
								aria-hidden="true"
							>
								{rows}
								{rows === 1 ? 'row' : 'rows'}
							</span>
						</div>
					{/if}
				{/if}
			</section>
		{/each}
	</div>
</div>
