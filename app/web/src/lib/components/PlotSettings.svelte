<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import SlidersHorizontalIcon from '@lucide/svelte/icons/sliders-horizontal';
	import InfoHint from '#lib/components/InfoHint.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import { NO_SMOOTHING, smooths } from '#lib/chart/series.ts';
	import SmoothingPicker from './SmoothingPicker.svelte';
	import XAxisPicker from './XAxisPicker.svelte';

	const ws = getWorkspace();
	let content = $state<HTMLElement | null>(null);
</script>

<!-- X axis, smoothing, outliers and rows of panels for every line plot of the workspace. -->
<Popover.Root>
	<Popover.Trigger>
		{#snippet child({ props })}
			<Button {...props} variant="outline" {@attach hint('Line plot settings')}>
				<SlidersHorizontalIcon /> Settings
			</Button>
		{/snippet}
	</Popover.Trigger>
	<!-- Focus starts on the popover, not in the x-axis picker, whose list would cover the rest. -->
	<Popover.Content
		bind:ref={content}
		align="start"
		class="w-80 gap-4"
		onOpenAutoFocus={(e) => {
			e.preventDefault();
			content?.focus();
		}}
	>
		<p class="text-xs text-muted-foreground">Line plots</p>
		<div class="grid gap-2">
			<div class="flex items-center gap-1.5">
				<Label>X axis</Label>
				<InfoHint label="About the x axis">
					For panels without their own. Step plots each metric against its step metric, when its run
					defined one. System metrics stay on time.
				</InfoHint>
			</div>
			<XAxisPicker value={ws.xAxis} onchange={(v) => (ws.xAxis = v ?? '_step')} />
		</div>
		<SmoothingPicker
			id="ws-smoothing"
			label="Smoothing"
			value={ws.smoothing}
			onchange={(v) => (ws.smoothing = v ?? NO_SMOOTHING)}
		/>
		<div class="flex items-center justify-between gap-4">
			<Label for="show-original" class={[!smooths(ws.smoothing) && 'opacity-50']}>
				Show original lines
			</Label>
			<Switch id="show-original" bind:checked={ws.showOriginal} disabled={!smooths(ws.smoothing)} />
		</div>
		<div class="flex items-center justify-between gap-4">
			<Label for="forecast">Forecast</Label>
			<Switch id="forecast" bind:checked={ws.forecast} />
		</div>
		<div class="flex items-center justify-between gap-4">
			<Label for="ignore-outliers">Ignore outliers when scaling</Label>
			<Switch id="ignore-outliers" bind:checked={ws.ignoreOutliers} />
		</div>
		<div class="flex items-center justify-between gap-4">
			<div class="flex items-center gap-1.5">
				<Label>Default rows per section</Label>
				<InfoHint label="About rows per section">
					Drag a section’s bottom edge to show more or fewer rows of it.
				</InfoHint>
			</div>
			<Select.Root
				type="single"
				value={String(ws.defaultRows)}
				onValueChange={(v) => (ws.defaultRows = Number(v))}
			>
				<Select.Trigger size="sm" class="w-20" aria-label="Default rows per section">
					{ws.defaultRows}
				</Select.Trigger>
				<Select.Content>
					{#each [1, 2, 3, 4, 6, 8] as n (n)}
						<Select.Item value={String(n)}>{n}</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
	</Popover.Content>
</Popover.Root>
