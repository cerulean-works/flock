<script lang="ts" module>
	/** One of a view's display settings: a choice from a list (a group's Group by, Color by, …). */
	export interface DisplaySetting {
		label: string;
		/** The choice; null for `none`. */
		value: string | null;
		options: { value: string | null; label: string }[];
		onchange: (value: string | null) => void;
		/** Choices are column keys: shown in the code font. */
		mono?: boolean;
		/** What the setting does, on its label's hover. */
		title?: string;
	}
</script>

<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import SlidersHorizontalIcon from '@lucide/svelte/icons/sliders-horizontal';
	import { hint } from '#lib/hint.svelte.ts';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
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
		/** How the runs are drawn, as rows above the rest (Linear's Display: grouping first). */
		display?: DisplaySetting[];
	}

	let { columns = null, charts = false, display = [] }: Props = $props();

	/** Radio items take strings: null travels as the empty string. */
	const NONE = '';
	const shown = (d: DisplaySetting) =>
		d.options.find((o) => o.value === d.value)?.label ?? d.value ?? 'none';

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
		{#if display.length}
			<div
				class="flex flex-col gap-0.5 border-b border-divider p-1.5"
				role="group"
				aria-label="Display"
			>
				{#each display as d (d.label)}
					<div class="flex h-8 items-center gap-2 pl-1.5 text-[13px]">
						<span class="min-w-0 flex-1 truncate text-muted-foreground" {@attach hint(d.title)}
							>{d.label}</span
						>
						<DropdownMenu.Root>
							<DropdownMenu.Trigger>
								{#snippet child({ props })}
									<Button
										{...props}
										variant="quiet"
										size="bar"
										class="max-w-40 gap-1 font-normal"
										aria-label="{d.label}: {shown(d)}"
									>
										<span
											class={[
												'min-w-0 truncate text-foreground',
												d.mono && d.value !== null && 'font-mono text-[12.5px]'
											]}>{shown(d)}</span
										>
										<ChevronDownIcon class="text-muted-foreground" />
									</Button>
								{/snippet}
							</DropdownMenu.Trigger>
							<DropdownMenu.Content
								align="end"
								collisionPadding={12}
								class="max-h-[min(20rem,var(--bits-dropdown-menu-content-available-height))] min-w-44 overflow-y-auto"
							>
								<DropdownMenu.RadioGroup
									value={d.value ?? NONE}
									onValueChange={(v) => d.onchange(v === NONE ? null : v)}
								>
									{#each d.options as o (o.value ?? NONE)}
										<DropdownMenu.RadioItem
											value={o.value ?? NONE}
											closeOnSelect
											class={d.mono && o.value !== null ? 'font-mono text-[12.5px]' : ''}
										>
											<span class="truncate">{o.label}</span>
										</DropdownMenu.RadioItem>
									{/each}
								</DropdownMenu.RadioGroup>
							</DropdownMenu.Content>
						</DropdownMenu.Root>
					</div>
				{/each}
			</div>
		{/if}
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
