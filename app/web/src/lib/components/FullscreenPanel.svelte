<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import XIcon from '@lucide/svelte/icons/x';
	import type { ResolvedPathname } from '$app/types';
	import { downloadCsv, downloadPng } from '#lib/chart/export.ts';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Dialog from '#lib/components/ui/dialog/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Separator } from '#lib/components/ui/separator/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import LinePlot from './LinePlot.svelte';
	import PanelLegend from './PanelLegend.svelte';
	import PanelMenu from './PanelMenu.svelte';
	import RunEye from './RunEye.svelte';

	interface Props {
		panel: number;
		closeHref: ResolvedPathname;
		prevHref: ResolvedPathname | null;
		nextHref: ResolvedPathname | null;
		onclose: (e?: MouseEvent) => void;
	}

	let { panel, closeHref, prevHref, nextHref, onclose }: Props = $props();

	const ws = getWorkspace();
	const view = $derived(ws.panels[panel]);
	const section = $derived(ws.sections.find((s) => s.key === view.section)!);
	const position = $derived(section.panels.indexOf(panel) + 1);

	let plot = $state<LinePlot>();
</script>

<!-- Rendered in place rather than portalled, so a full-screen deep link is server-rendered. -->
<Dialog.Root open onOpenChange={(open) => !open && onclose()}>
	<Dialog.Content
		portalProps={{ disabled: true }}
		showCloseButton={false}
		class="fullscreen fixed inset-0 top-0 left-0 flex max-w-none translate-0 flex-col gap-0 rounded-none bg-background p-0 ring-0 sm:max-w-none"
	>
		<header class="flex items-end gap-4 border-b border-divider px-7 pt-5 pb-4">
			<div class="min-w-0 flex-1">
				<Dialog.Description class="text-[13px]">
					{view.section} · {position} of {section.panels.length}
				</Dialog.Description>
				<Dialog.Title
					class="mt-0.5 truncate font-serif text-[34px] leading-[1.15] font-normal tracking-[-0.02em]"
				>
					{view.title}
				</Dialog.Title>
			</div>
			<div class="flex items-center gap-1.5">
				{#if ws.zoomOf(panel) != null}
					<Button variant="outline" onclick={() => ws.setZoom(panel, null)}>Reset zoom</Button>
				{/if}
				<div class="flex items-center gap-2 px-2">
					<Switch
						id="fullscreen-log"
						checked={ws.isLog(panel)}
						onCheckedChange={() => ws.toggleLog(panel)}
					/>
					<Label for="fullscreen-log">Log scale</Label>
				</div>
				<Button variant="outline" onclick={() => ws.editPanel(panel)}>
					<PencilIcon /> Edit panel
				</Button>
				<PanelMenu
					{panel}
					logToggle={false}
					onpng={() => downloadPng(plot?.snapshot() ?? null, view.title)}
					oncsv={() => downloadCsv(ws, panel)}
				/>
				<Separator orientation="vertical" class="mx-1.5 h-5! data-vertical:self-center" />
				<Button
					variant="ghost"
					size="icon-sm"
					href={prevHref ?? closeHref}
					disabled={!prevHref}
					class="aria-disabled:pointer-events-none aria-disabled:opacity-40"
					aria-label="Previous panel"
					title="Previous panel (←)"
					data-sveltekit-replacestate
				>
					<ChevronLeftIcon />
				</Button>
				<Button
					variant="ghost"
					size="icon-sm"
					href={nextHref ?? closeHref}
					disabled={!nextHref}
					class="aria-disabled:pointer-events-none aria-disabled:opacity-40"
					aria-label="Next panel"
					title="Next panel (→)"
					data-sveltekit-replacestate
				>
					<ChevronRightIcon />
				</Button>
				<Button
					variant="ghost"
					size="icon-sm"
					href={closeHref}
					onclick={onclose}
					aria-label="Close full screen"
					title="Close (Esc)"
				>
					<XIcon />
				</Button>
			</div>
		</header>
		<div class="flex min-h-0 flex-1">
			<div class="flex min-w-0 flex-1 flex-col pt-6 pr-6 pb-3 pl-3">
				<PanelLegend {view} metricsOnly class="mb-3 ml-16 text-[13px]" />
				<div class="min-h-0 flex-1">
					<LinePlot bind:this={plot} {panel} surface="fullscreen" active={true} />
				</div>
				<p class="mt-2.5 ml-16 text-xs text-faint">
					Drag to zoom · double-click to reset · click a line to pin its run
				</p>
			</div>
			<!-- As wide as a run's overview, its text on the same 16px inset as its heading. -->
			<aside class="flex w-[22rem] flex-none flex-col border-l border-divider" aria-label="Runs">
				<p class="px-4 pt-3.5 pb-2.5 text-[13px] text-muted-foreground">
					{ws.visible.length} of {ws.page.runs.length} runs plotted
				</p>
				<ul class="flex-1 overflow-y-auto px-2 pb-4">
					{#each ws.page.runs as run, row (run.id)}
						{@const shown = !ws.hidden.has(run.id)}
						<li
							class={[
								'flex h-[30px] items-center gap-2 rounded-md px-2 text-[13px]',
								ws.focus === row && 'bg-accent'
							]}
							onpointerenter={() => (ws.hover = row)}
							onpointerleave={() => (ws.hover = null)}
						>
							<RunEye
								color={ws.colors[row]}
								{shown}
								label={shown ? `Hide ${run.name}` : `Show ${run.name}`}
								onclick={() => ws.toggleRun(row)}
							/>
							<button
								class={[
									'min-w-0 flex-1 truncate text-left',
									shown ? 'text-foreground' : 'text-faint'
								]}
								onclick={() => ws.togglePin(row)}
								title={run.name}
							>
								{run.name}
							</button>
							{#if ws.pinned === row}
								<Badge variant="secondary" class="text-muted-foreground">Pinned</Badge>
							{/if}
						</li>
					{/each}
				</ul>
			</aside>
		</div>
	</Dialog.Content>
</Dialog.Root>
