<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Settings2Icon from '@lucide/svelte/icons/settings-2';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import PinIcon from '@lucide/svelte/icons/pin';
	import PinOffIcon from '@lucide/svelte/icons/pin-off';
	import { pins } from '#lib/home/pins.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	interface Props {
		panel: number;
		onpng: () => void;
		oncsv: () => void;
		/** Hide the log-scale toggle where the panel already shows one. */
		logToggle?: boolean;
	}

	let { panel, onpng, oncsv, logToggle = true }: Props = $props();

	const ws = getWorkspace();

	// On a run's own page (a workspace of one), a logged curve can go to Home (H10): the panel's
	// first metric, for this run.
	const pin = $derived.by(() => {
		const p = ws.panels[panel];
		const run = ws.page.runs.length === 1 ? ws.page.runs[0] : null;
		const metric = p && p.stream === 'history' ? ws.meta.metrics[p.metrics[0]]?.key : undefined;
		if (!run || !metric || ws.page.projectTotal !== 1) return null;
		return {
			org: ws.meta.entity,
			project: ws.meta.project,
			run: run.id,
			label: run.name,
			metric
		};
	});
	const pinned = $derived(pin ? pins.has(pin) : false);
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant="ghost"
				size="icon-sm"
				aria-label="Panel settings"
				{@attach hint('Panel settings')}
			>
				<Settings2Icon />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-56">
		<DropdownMenu.Group>
			<DropdownMenu.Label class="truncate">{ws.panels[panel].title}</DropdownMenu.Label>
			<DropdownMenu.Item onSelect={() => ws.editPanel(panel)}>
				<PencilIcon /> Edit panel…
			</DropdownMenu.Item>
			{#if logToggle}
				<DropdownMenu.CheckboxItem
					checked={ws.isLog(panel)}
					onCheckedChange={() => ws.toggleLog(panel)}
				>
					Log scale (y)
				</DropdownMenu.CheckboxItem>
			{/if}
			<DropdownMenu.Item
				disabled={ws.zoomOf(panel) == null}
				onSelect={() => ws.setZoom(panel, null)}
			>
				Reset zoom
			</DropdownMenu.Item>
		</DropdownMenu.Group>
		{#if pin}
			{@const p = pin}
			<DropdownMenu.Separator />
			<DropdownMenu.Item onSelect={() => pins.toggle(p)}>
				{#if pinned}<PinOffIcon /> Unpin from Home{:else}<PinIcon /> Pin to Home{/if}
			</DropdownMenu.Item>
		{/if}
		<DropdownMenu.Separator />
		<DropdownMenu.Group>
			<DropdownMenu.Item onSelect={onpng}><DownloadIcon /> Download PNG</DropdownMenu.Item>
			<DropdownMenu.Item onSelect={oncsv}><DownloadIcon /> Download CSV</DropdownMenu.Item>
		</DropdownMenu.Group>
		{#if ws.isCustom(panel)}
			<DropdownMenu.Separator />
			<DropdownMenu.Item variant="destructive" onSelect={() => ws.deletePanel(panel)}>
				<Trash2Icon /> Delete panel
			</DropdownMenu.Item>
		{/if}
	</DropdownMenu.Content>
</DropdownMenu.Root>
