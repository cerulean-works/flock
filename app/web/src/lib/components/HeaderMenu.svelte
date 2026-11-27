<script lang="ts">
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ArrowUpDownIcon from '@lucide/svelte/icons/arrow-up-down';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import { hint } from '#lib/hint.svelte.ts';
	import MenuSearch from '#lib/components/MenuSearch.svelte';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { isInternalKey, metricLabel, type BestOrder } from '#lib/groups.ts';
	import type { SortOrder } from '#lib/types.ts';

	/**
	 * A column header's menu, beside its filter funnel: how to sort by the column, and, where
	 * runs are ranked (a group's page, Compare, the group editor), whether it's the column Best
	 * by reads and which way (D217). Its trigger is the sort's arrow: ↑ or ↓ while the column
	 * sorts the list, a faint ↕ on the header's hover otherwise (a chevron when it can't sort).
	 * The header must be a `group/h`.
	 */

	interface Props {
		/** The column's name, for the trigger's label. */
		label: string;
		sort?: {
			/** The way the column sorts the list, or null when another column does. */
			dir: SortOrder | null;
			onsort: (order: SortOrder) => void;
			/** Back to the list's own order; offered while the column sorts it. */
			onclear?: () => void;
		} | null;
		best?: {
			/** The column is the one Best by reads. */
			active: boolean;
			order: BestOrder | null;
			onbest: (order: BestOrder) => void;
			/** Other columns to rank by, in a submenu (a header that stands for the setting). */
			keys?: readonly string[];
			onpick?: (key: string) => void;
		} | null;
		disabled?: boolean;
	}

	let { label, sort = null, best = null, disabled = false }: Props = $props();

	const dir = $derived(sort?.dir ?? null);
	const what = $derived(sort ? `Sort ${label.toLowerCase()}` : `${label} options`);

	let query = $state('');
	const others = $derived(
		(best?.keys ?? []).filter(
			(k) =>
				!isInternalKey(k) && (!query.trim() || k.toLowerCase().includes(query.trim().toLowerCase()))
		)
	);
	const SHOWN = 200;
	/** Radio groups take strings: nothing picked travels as the empty string. */
	const NONE = '';
</script>

<DropdownMenu.Root onOpenChange={() => (query = '')}>
	<DropdownMenu.Trigger {disabled}>
		{#snippet child({ props })}
			<button
				{...props}
				type="button"
				class={[
					'relative grid h-5 flex-none place-items-center overflow-hidden rounded outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none data-[state=open]:bg-muted',
					// Room only while it's wanted: on the header's hover or focus, open, or sorting,
					// so a narrow column's name isn't cut for an icon nobody sees.
					dir
						? 'w-5 text-foreground'
						: 'w-0 text-faint opacity-0 group-focus-within/h:w-5 group-focus-within/h:opacity-100 group-hover/h:w-5 group-hover/h:opacity-100 focus-visible:w-5 focus-visible:opacity-100 data-[state=open]:w-5 data-[state=open]:opacity-100 pointer-coarse:w-5 pointer-coarse:opacity-100'
				]}
				aria-label={dir ? `${what}, ${dir === 'asc' ? 'ascending' : 'descending'}` : what}
				{@attach hint(what)}
				onpointerdown={(e) => {
					// Not a press on the header (which drags or sorts), but still the menu's own.
					e.stopPropagation();
					(props.onpointerdown as ((e: PointerEvent) => void) | undefined)?.(e);
				}}
			>
				{#if dir === 'asc'}
					<ArrowUpIcon class="size-3" />
				{:else if dir === 'desc'}
					<ArrowDownIcon class="size-3" />
				{:else if sort}
					<ArrowUpDownIcon class="size-3" />
				{:else}
					<ChevronDownIcon class="size-3" />
				{/if}
			</button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-52 text-[13px] font-normal text-foreground">
		{#if sort}
			<DropdownMenu.RadioGroup
				value={dir ?? NONE}
				onValueChange={(v) => v && sort.onsort(v as SortOrder)}
			>
				<DropdownMenu.RadioItem value="asc" closeOnSelect>
					<ArrowUpIcon class="text-muted-foreground" /> Sort ascending
				</DropdownMenu.RadioItem>
				<DropdownMenu.RadioItem value="desc" closeOnSelect>
					<ArrowDownIcon class="text-muted-foreground" /> Sort descending
				</DropdownMenu.RadioItem>
			</DropdownMenu.RadioGroup>
			{#if dir && sort.onclear}
				<DropdownMenu.Item onSelect={sort.onclear}>
					<span class="size-4" aria-hidden="true"></span> Clear sort
				</DropdownMenu.Item>
			{/if}
		{/if}
		{#if best}
			{#if sort}<DropdownMenu.Separator />{/if}
			<DropdownMenu.RadioGroup
				value={best.active ? (best.order ?? NONE) : NONE}
				onValueChange={(v) => v && best.onbest(v as BestOrder)}
			>
				<DropdownMenu.RadioItem value="asc" closeOnSelect>Best is lowest</DropdownMenu.RadioItem>
				<DropdownMenu.RadioItem value="desc" closeOnSelect>Best is highest</DropdownMenu.RadioItem>
			</DropdownMenu.RadioGroup>
			{#if best.keys && best.onpick}
				{@const onpick = best.onpick}
				<DropdownMenu.Separator />
				<DropdownMenu.Sub>
					<DropdownMenu.SubTrigger>Rank by another column</DropdownMenu.SubTrigger>
					<DropdownMenu.SubContent class="w-64 p-0">
						<!-- Typing here filters the columns; keys go to the field, not the menu's typeahead. -->
						<MenuSearch
							placeholder="Search columns…"
							label="Search summary columns"
							bind:value={query}
							onkeydown={(e) => {
								if (e.key !== 'Escape' && e.key !== 'Tab') e.stopPropagation();
							}}
						/>
						<div class="max-h-64 overflow-y-auto p-1">
							{#each others.slice(0, SHOWN) as k (k)}
								<DropdownMenu.Item onSelect={() => onpick(k)}>
									<span class="truncate">{metricLabel(k)}</span>
								</DropdownMenu.Item>
							{:else}
								<p class="px-2 py-1.5 text-xs text-muted-foreground">No matching columns</p>
							{/each}
						</div>
					</DropdownMenu.SubContent>
				</DropdownMenu.Sub>
			{/if}
		{/if}
	</DropdownMenu.Content>
</DropdownMenu.Root>
