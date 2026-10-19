<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
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
	// Moving panels. A panel follows the pointer while the others make room for it; it lands
	// where it is dropped. Its section's order is then kept for this project.

	/** The panel being dragged, and how far it is from its place in the grid. */
	let drag = $state<{ metric: number; x: number; y: number } | null>(null);

	const EDGE = 56; // px from the pane's top or bottom where dragging scrolls it

	function grab(e: PointerEvent, section: string, metric: number) {
		const target = e.target as HTMLElement;
		if (
			e.button !== 0 ||
			(target.closest('button, a, [role="menu"]') && !target.closest('[data-grip]'))
		)
			return;
		e.preventDefault();
		const handle = e.currentTarget as HTMLElement;
		const card = handle.closest<HTMLElement>('[data-panel]')!;
		const grid = card.parentElement!;
		handle.setPointerCapture(e.pointerId);
		const start = { x: e.clientX, y: e.clientY };
		const box = card.getBoundingClientRect();
		const offset = { x: e.clientX - box.left, y: e.clientY - box.top };
		let pointer = start;
		let frame = 0;

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
					ws.movePanel(section, metric, to);
					void tick().then(follow);
					return;
				}
			}
		}

		/** Near the pane's edges, scroll it (and keep following). */
		function scroll() {
			frame = 0;
			if (!drag || !root) return;
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
			pointer = { x: ev.clientX, y: ev.clientY };
			if (!drag) {
				if (Math.hypot(pointer.x - start.x, pointer.y - start.y) < 4) return;
				drag = { metric, x: 0, y: 0 };
				ws.cursor = null;
				ws.hover = null;
			}
			follow();
			if (!frame) frame = requestAnimationFrame(scroll);
		};
		const up = () => {
			cancelAnimationFrame(frame);
			drag = null;
			handle.removeEventListener('pointermove', move);
			handle.removeEventListener('pointerup', up);
			handle.removeEventListener('pointercancel', up);
		};
		handle.addEventListener('pointermove', move);
		handle.addEventListener('pointerup', up);
		handle.addEventListener('pointercancel', up);
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
		ws.movePanel(section, metric, i + by);
		await tick();
		root?.querySelector<HTMLElement>(`[data-panel="${metric}"] [data-grip]`)?.focus();
	}

	const settle = $derived(prefersReducedMotion.current ? 0 : 200);
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
	{#each ws.sections as sec (sec.key)}
		{@const count = sec.metrics.length}
		{@const p = pageOf(sec.key, count)}
		{@const pages = Math.ceil(count / ws.panelsPerPage)}
		{@const from = p * ws.panelsPerPage}
		{@const slice = sec.metrics.slice(from, from + ws.panelsPerPage)}
		{@const open = !ws.collapsed.has(sec.key)}
		<section class="mt-2 first:mt-0">
			<!-- Edge to edge, so the first one continues the runs table's header line. -->
			<header class="-mx-5 flex h-(--section-h) items-center gap-1 border-b px-5">
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
				<div class="relative grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-3 pt-3 pb-2">
					{#each slice as m (m)}
						{@const dragging = drag?.metric === m}
						<div
							class={['min-w-0', dragging ? 'relative z-2' : 'transition-transform']}
							style:transform={dragging ? `translate(${drag!.x}px, ${drag!.y}px)` : null}
							style:transition-duration={dragging ? null : `${settle}ms`}
							data-panel={m}
							animate:flip={{ duration: dragging ? 0 : settle }}
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
