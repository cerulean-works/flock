<script lang="ts">
	import SlidersHorizontalIcon from '@lucide/svelte/icons/sliders-horizontal';
	import { hint } from '#lib/hint.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import { LOCKED_COLUMNS } from '#lib/workspace/columns.ts';
	import { getWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import ColumnsMenu from './ColumnsMenu.svelte';
	import PlotSettings from './PlotSettings.svelte';

	interface Props {
		/** The table shows: its columns to show, hide, reorder or jump to (`keys`: only those). */
		columns?: { keys?: readonly string[]; onjump: (key: string) => void } | null;
		/** The charts show: their line plot settings. */
		charts?: boolean;
	}

	let { columns = null, charts = false }: Props = $props();

	const ws = getWorkspace();
	let open = $state(false);
	let tab = $state<'columns' | 'charts'>('columns');
	const both = $derived(!!columns && charts);
	const showing = $derived(!columns ? 'charts' : !charts ? 'columns' : tab);

	const hiddenCount = $derived.by(() => {
		if (!columns) return 0;
		const keys = columns.keys ?? ws.meta.columns.map((c) => c.key);
		return keys.filter((k) => ws.hiddenColumns.has(k) && !LOCKED_COLUMNS.has(k)).length;
	});
	const label = $derived(
		both ? 'Columns and line plots' : columns ? 'Columns' : 'Line plot settings'
	);
</script>

<!-- The view's settings, as Linear's Display: the table's columns, the charts' settings, or
     both under two tabs when both show. -->
<Popover.Root bind:open>
	<Popover.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant={hiddenCount ? 'quiet-on' : 'quiet'}
				size="icon-bar"
				aria-label={hiddenCount ? `${label}, ${hiddenCount} columns hidden` : label}
				{@attach hint(hiddenCount ? `${label}: ${hiddenCount} columns hidden` : label)}
			>
				<SlidersHorizontalIcon />
			</Button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content
		align="end"
		collisionPadding={16}
		class="flex w-80 flex-col gap-0 p-0"
		onOpenAutoFocus={(e) => e.preventDefault()}
	>
		{#if both}
			<div class="flex h-9 flex-none gap-4 border-b border-divider px-3" role="tablist">
				{#each [['columns', 'Columns'], ['charts', 'Line plots']] as const as [value, name] (value)}
					<button
						type="button"
						role="tab"
						aria-selected={tab === value}
						class={[
							'-mb-px border-b-2 text-[13px] outline-none focus-visible:text-foreground',
							tab === value
								? 'border-foreground text-foreground'
								: 'border-transparent text-muted-foreground hover:text-foreground'
						]}
						onclick={() => (tab = value)}
					>
						{name}
					</button>
				{/each}
			</div>
		{/if}
		{#if showing === 'columns' && columns}
			<ColumnsMenu keys={columns.keys} onjump={columns.onjump} onclose={() => (open = false)} />
		{:else}
			<PlotSettings embedded />
		{/if}
	</Popover.Content>
</Popover.Root>
