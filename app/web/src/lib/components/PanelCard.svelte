<script lang="ts">
	import GripVerticalIcon from '@lucide/svelte/icons/grip-vertical';
	import Maximize2Icon from '@lucide/svelte/icons/maximize-2';
	import type { ResolvedPathname } from '$app/types';
	import { downloadCsv, downloadPng } from '#lib/chart/export.ts';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import { getWorkspace, MAX_PLOT_H, MIN_PLOT_H } from '#lib/workspace/workspace.svelte.ts';
	import LinePlot from './LinePlot.svelte';
	import PanelLegend from './PanelLegend.svelte';
	import PanelMenu from './PanelMenu.svelte';

	interface Props {
		panel: number;
		active: boolean;
		root: HTMLElement | null;
		fullscreenHref: ResolvedPathname;
		onfullscreen: (e: MouseEvent) => void;
		/** Picked up to be moved (pointer down on the header); the grid does the moving. */
		ongrab: (e: PointerEvent) => void;
		/** Moved one place earlier or later from the keyboard. */
		onnudge: (by: -1 | 1) => void;
		/** Being dragged: lifted above the others. */
		dragging?: boolean;
	}

	let {
		panel,
		active,
		root,
		fullscreenHref,
		onfullscreen,
		ongrab,
		onnudge,
		dragging = false
	}: Props = $props();

	const ws = getWorkspace();
	const view = $derived(ws.panels[panel]);
	const section = $derived(view.section);

	const zoomed = $derived(ws.zoomOf(panel) != null);
	// Panels of a section share one height, so its rows stay even.
	const height = $derived(ws.heightOf(section));

	let plot = $state<LinePlot>();
	let resizing = $state(false);

	/** Drag the bottom edge: every panel of the section follows. */
	function startResize(e: PointerEvent) {
		if (e.button !== 0) return;
		e.preventDefault();
		const el = e.currentTarget as HTMLElement;
		el.setPointerCapture(e.pointerId);
		const y0 = e.clientY;
		const h0 = height;
		const sec = section;
		resizing = true;
		ws.cursor = null;
		const move = (ev: PointerEvent) => ws.setHeight(sec, h0 + ev.clientY - y0, false);
		const up = (ev: PointerEvent) => {
			ws.setHeight(sec, h0 + ev.clientY - y0);
			resizing = false;
			el.removeEventListener('pointermove', move);
			el.removeEventListener('pointerup', up);
			el.removeEventListener('pointercancel', up);
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', up);
		el.addEventListener('pointercancel', up);
	}

	function resizeKey(e: KeyboardEvent) {
		const by = { ArrowUp: -16, ArrowDown: 16 }[e.key];
		if (by == null) return;
		e.preventDefault();
		ws.setHeight(section, height + by);
	}

	function gripKey(e: KeyboardEvent) {
		const by = ({ ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 } as const)[
			e.key as 'ArrowLeft'
		];
		if (by == null) return;
		e.preventDefault();
		onnudge(by);
	}
</script>

<Card.Root
	size="sm"
	class={[
		'card relative min-w-0 gap-1.5 pb-2 transition-shadow',
		dragging && 'shadow-xl ring-foreground/20'
	]}
	aria-label={view.title}
>
	<!-- The header is the handle; its buttons keep their clicks. -->
	<Card.Header
		class={['min-h-7 items-center', dragging ? 'cursor-grabbing' : 'cursor-grab']}
		onpointerdown={ongrab}
	>
		<Card.Title class="flex min-w-0 items-center leading-7" title={view.title}>
			<button
				class={[
					'-ml-1.5 grid h-6 w-4 flex-none cursor-[inherit] place-items-center rounded text-faint transition-opacity outline-none hover:text-muted-foreground focus-visible:opacity-100 focus-visible:ring-3 focus-visible:ring-ring/50',
					dragging ? 'opacity-100' : 'opacity-0 group-hover/card:opacity-100'
				]}
				data-grip
				aria-label="Move {view.title} (arrow keys)"
				title="Drag to move"
				onkeydown={gripKey}
			>
				<GripVerticalIcon class="size-3.5" />
			</button>
			<span class="min-w-0 truncate">
				<span class="font-normal text-muted-foreground">{view.title.slice(0, view.prefix)}</span
				>{view.title.slice(view.prefix)}
			</span>
		</Card.Title>
		<Card.Action
			class={[
				'flex cursor-default items-center gap-0.5 self-center transition-opacity duration-150',
				'group-focus-within/card:opacity-100 group-hover/card:opacity-100 has-aria-expanded:opacity-100',
				zoomed ? 'opacity-100' : 'opacity-0'
			]}
		>
			{#if zoomed}
				<Button variant="link" size="xs" onclick={() => ws.setZoom(panel, null)}>Reset zoom</Button>
			{/if}
			<Button
				variant="ghost"
				size="icon-sm"
				href={fullscreenHref}
				onclick={onfullscreen}
				title="Full screen"
				aria-label="{view.title} full screen"
			>
				<Maximize2Icon />
			</Button>
			<PanelMenu
				{panel}
				onpng={() => downloadPng(plot?.snapshot() ?? null, view.title)}
				oncsv={() => downloadCsv(ws, panel)}
			/>
		</Card.Action>
	</Card.Header>
	<Card.Content class="flex flex-col gap-1.5 pl-1.5">
		<PanelLegend {view} />
		<div style:height="{height}px">
			<LinePlot bind:this={plot} {panel} surface="grid" {active} {root} />
		</div>
	</Card.Content>
	<!-- Resizes the section's panels; double-click goes back to the default height. A focusable
	     separator is ARIA's window splitter: arrow keys resize too. -->
	<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
	<div
		class={[
			'group/resize absolute inset-x-0 bottom-0 z-1 grid h-2.5 cursor-row-resize touch-none place-items-center outline-none',
			'after:h-[3px] after:w-10 after:rounded-full after:transition-colors focus-visible:after:bg-ring',
			resizing ? 'after:bg-ring' : 'after:bg-transparent hover:after:bg-border'
		]}
		role="separator"
		aria-orientation="horizontal"
		aria-label="Resize {section} panels"
		aria-valuenow={height}
		aria-valuemin={MIN_PLOT_H}
		aria-valuemax={MAX_PLOT_H}
		tabindex="0"
		title="Drag to resize · double-click to reset"
		onpointerdown={startResize}
		ondblclick={() => ws.setHeight(section, null)}
		onkeydown={resizeKey}
	></div>
</Card.Root>
