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
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import PanelCard from './PanelCard.svelte';
	import { BAND_H, HEAD_H } from './RunsTable.svelte';

	interface Props {
		shown: boolean;
		/** Plots are live (shown and not covered by a full-screen panel). */
		active: boolean;
		panelHref: (metric: number) => ResolvedPathname;
		onfullscreen: (metric: number, e: MouseEvent) => void;
		/**
		 * Beside the runs table (the workspace), whose header the first section's lines up with;
		 * otherwise the pane fills its container (a run's page).
		 */
		beside?: boolean;
	}

	let { shown, active, panelHref, onfullscreen, beside = true }: Props = $props();

	const ws = getWorkspace();
	let root = $state<HTMLDivElement | null>(null);

	function pageOf(key: string, count: number) {
		const pages = Math.max(1, Math.ceil(count / ws.panelsPerPage));
		return Math.min(ws.sectionPage[key] ?? 0, pages - 1);
	}

	// ------------------------------------------------------------------------------------------
	// Moving panels and sections. What's dragged follows the pointer while the others make room
	// for it; it lands where it is dropped. The order is then kept for this project.

	/** The panel being dragged, and how far it is from its place in the grid. */
	let drag = $state<{ metric: number; x: number; y: number } | null>(null);
	/** The section being dragged, and how far it is from its place in the list. */
	let sectionDrag = $state<{ key: string; y: number } | null>(null);
	let list = $state<HTMLDivElement | null>(null);

	const EDGE = 56; // px from the pane's top or bottom where dragging scrolls it

	/**
	 * Follow a drag from `handle`. Once the pointer has moved a few px, `begin`; then `follow`
	 * on every move, and while the pane scrolls under a pointer held near its top or bottom;
	 * `end` on release. `pointer` is kept current, so `follow` can run again later.
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
			if (!started || !root) return;
			const r = root.getBoundingClientRect();
			const by =
				pointer.y < r.top + EDGE
					? -(r.top + EDGE - pointer.y) / 4
					: pointer.y > r.bottom - EDGE
						? (pointer.y - (r.bottom - EDGE)) / 4
						: 0;
			if (!by) return;
			root.scrollTop += by;
			follow();
			frame = requestAnimationFrame(scroll);
		}

		const move = (ev: PointerEvent) => {
			pointer.x = ev.clientX;
			pointer.y = ev.clientY;
			if (!started) {
				if (Math.hypot(pointer.x - start.x, pointer.y - start.y) < 4) return;
				started = true;
				ws.cursor = null;
				ws.hover = null;
				begin();
			}
			follow();
			if (!frame) frame = requestAnimationFrame(scroll);
		};
		const up = () => {
			cancelAnimationFrame(frame);
			started = false;
			end();
			handle.removeEventListener('pointermove', move);
			handle.removeEventListener('pointerup', up);
			handle.removeEventListener('pointercancel', up);
		};
		handle.addEventListener('pointermove', move);
		handle.addEventListener('pointerup', up);
		handle.addEventListener('pointercancel', up);
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

	function grab(e: PointerEvent, section: string, metric: number) {
		if (!grabs(e)) return;
		e.preventDefault();
		const handle = e.currentTarget as HTMLElement;
		const card = handle.closest<HTMLElement>('[data-panel]')!;
		const grid = card.parentElement!;
		const box = card.getBoundingClientRect();
		const offset = { x: e.clientX - box.left, y: e.clientY - box.top };

		/** Follow the pointer, and move the panel to the slot under it. */
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
						.metrics.indexOf(Number(el.dataset.panel));
					rearrange();
					ws.movePanel(section, metric, to);
					void tick().then(follow);
					return;
				}
			}
		}

		const pointer = track(e, handle, {
			begin: () => (drag = { metric, x: 0, y: 0 }),
			follow,
			end: () => (drag = null)
		});
	}

	/** Keyboard moves, keeping focus on the panel's grip as it changes place. */
	async function nudge(section: string, metric: number, by: -1 | 1) {
		const metrics = ws.sections.find((s) => s.key === section)!.metrics;
		const i = metrics.indexOf(metric);
		if (i + by < 0 || i + by >= metrics.length) return;
		// A panel moved past its page's edge goes to the next (or previous) page with it.
		const per = ws.panelsPerPage;
		if (Math.floor(i / per) !== Math.floor((i + by) / per))
			ws.setSectionPage(section, Math.floor((i + by) / per));
		rearrange();
		ws.movePanel(section, metric, i + by);
		await tick();
		root?.querySelector<HTMLElement>(`[data-panel="${metric}"] [data-grip]`)?.focus();
	}

	function grabSection(e: PointerEvent, key: string) {
		if (!grabs(e) || !list) return;
		e.preventDefault();
		const handle = e.currentTarget as HTMLElement;
		const el = handle.closest<HTMLElement>('[data-section]')!;
		const box = el.getBoundingClientRect();
		const offsetY = e.clientY - box.top;
		const sections = list;

		/**
		 * Follow the pointer, and move the section past every other whose middle the pointer is
		 * below. Sections differ in height, so slots are halves, not the boxes themselves.
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

		const pointer = track(e, handle, {
			begin: () => (sectionDrag = { key, y: 0 }),
			follow,
			end: () => (sectionDrag = null)
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
     painting in idle time) while hidden behind the table. -->
<div
	class={[
		'pane absolute inset-y-0 right-0 z-4 overflow-y-auto overscroll-contain bg-background px-5 pb-10',
		beside ? 'left-(--name-w) shadow-[-1px_0_0_var(--divider)]' : 'left-0',
		'transition-[opacity,translate,visibility] duration-150 ease-out',
		shown ? 'visible translate-x-0 opacity-100' : 'invisible translate-x-3 opacity-0'
	]}
	style:padding-top="{beside ? BAND_H : 8}px"
	style:--section-h="{beside ? HEAD_H : 48}px"
	bind:this={root}
	inert={!shown}
	onscroll={() => {
		if (ws.cursor) ws.cursor = null;
	}}
>
	<div class="relative" bind:this={list}>
		{#each ws.sections as sec (sec.key)}
			{@const count = sec.metrics.length}
			{@const p = pageOf(sec.key, count)}
			{@const pages = Math.ceil(count / ws.panelsPerPage)}
			{@const from = p * ws.panelsPerPage}
			{@const slice = sec.metrics.slice(from, from + ws.panelsPerPage)}
			{@const open = !ws.collapsed.has(sec.key)}
			{@const moving = sectionDrag?.key === sec.key}
			<!-- A section being dragged folds to its header (its panels stay mounted), so the others
			     are within reach. -->
			<section
				class={['mt-2 first:mt-0', moving ? 'relative z-3' : 'transition-transform']}
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
						<h2 class="font-serif text-[22px] font-normal tracking-[-0.01em]">{sec.key}</h2>
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
							disabled={p === 0}
							onclick={() => ws.setSectionPage(sec.key, p - 1)}
						>
							<ChevronLeftIcon />
						</Button>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label="Next panels"
							disabled={p >= pages - 1}
							onclick={() => ws.setSectionPage(sec.key, p + 1)}
						>
							<ChevronRightIcon />
						</Button>
					{/if}
				</header>
				{#if open}
					<div
						class={[
							'relative grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-3 pt-3 pb-2',
							moving && 'hidden'
						]}
					>
						{#each slice as m (m)}
							{@const dragging = drag?.metric === m}
							<div
								class={['min-w-0', dragging ? 'relative z-2' : 'transition-transform']}
								style:transform={dragging ? `translate(${drag!.x}px, ${drag!.y}px)` : null}
								style:transition-duration={dragging ? null : `${settle}ms`}
								data-panel={m}
								animate:flip={{ duration: dragging || !rearranging ? 0 : settle }}
							>
								<PanelCard
									metric={m}
									{active}
									{root}
									{dragging}
									fullscreenHref={panelHref(m)}
									onfullscreen={(e) => onfullscreen(m, e)}
									ongrab={(e) => grab(e, sec.key, m)}
									onnudge={(by) => nudge(sec.key, m, by)}
								/>
							</div>
						{/each}
					</div>
				{/if}
			</section>
		{/each}
	</div>
</div>
