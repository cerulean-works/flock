<script lang="ts">
	import DownloadIcon from '@lucide/svelte/icons/download';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import Settings2Icon from '@lucide/svelte/icons/settings-2';
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
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant="ghost"
				size="icon-sm"
				aria-label="Panel settings"
				title="Panel settings"
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
		<DropdownMenu.Separator />
		<DropdownMenu.Group>
			<DropdownMenu.Item onSelect={onpng}><DownloadIcon /> Download PNG</DropdownMenu.Item>
			<DropdownMenu.Item onSelect={oncsv}><DownloadIcon /> Download CSV</DropdownMenu.Item>
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>
