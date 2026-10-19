<script lang="ts">
	import SlidersHorizontalIcon from '@lucide/svelte/icons/sliders-horizontal';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import * as Select from '#lib/components/ui/select/index.js';
	import { Slider } from '#lib/components/ui/slider/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';

	const ws = getWorkspace();
</script>

<!-- Smoothing, outliers and paging for every line plot of the workspace. -->
<Popover.Root>
	<Popover.Trigger>
		{#snippet child({ props })}
			<Button {...props} variant="outline" title="Line plot settings">
				<SlidersHorizontalIcon /> Settings
			</Button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content align="start" class="w-80 gap-4">
		<p class="text-xs text-muted-foreground">Line plots</p>
		<div class="grid gap-3">
			<div class="flex items-center justify-between">
				<Label>Smoothing</Label>
				<output class="text-muted-foreground tabular-nums">{ws.smoothing.toFixed(2)}</output>
			</div>
			<Slider
				type="single"
				bind:value={ws.smoothing}
				min={0}
				max={0.99}
				step={0.01}
				aria-label="Smoothing"
			/>
		</div>
		<div class="flex items-center justify-between gap-4">
			<Label for="show-original" class={[ws.smoothing === 0 && 'opacity-50']}>
				Show original lines
			</Label>
			<Switch id="show-original" bind:checked={ws.showOriginal} disabled={ws.smoothing === 0} />
		</div>
		<div class="flex items-center justify-between gap-4">
			<Label for="ignore-outliers">Ignore outliers when scaling</Label>
			<Switch id="ignore-outliers" bind:checked={ws.ignoreOutliers} />
		</div>
		<div class="flex items-center justify-between gap-4">
			<Label>Panels per page</Label>
			<Select.Root
				type="single"
				value={String(ws.panelsPerPage)}
				onValueChange={(v) => (ws.panelsPerPage = Number(v))}
			>
				<Select.Trigger size="sm" class="w-20" aria-label="Panels per page">
					{ws.panelsPerPage}
				</Select.Trigger>
				<Select.Content>
					{#each [6, 9, 12, 24] as n (n)}
						<Select.Item value={String(n)}>{n}</Select.Item>
					{/each}
				</Select.Content>
			</Select.Root>
		</div>
	</Popover.Content>
</Popover.Root>
