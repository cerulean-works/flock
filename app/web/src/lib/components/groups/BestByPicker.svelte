<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import TrophyIcon from '@lucide/svelte/icons/trophy';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { bestLabel, bestWords, metricLabel, type BestOrder } from '#lib/groups.ts';

	/** **Best by**: a summary column and lowest or highest (D184). */

	interface Props {
		key: string | null;
		order: BestOrder | null;
		/** The `summary.*` columns to pick from. */
		keys: readonly string[];
		onchange: (key: string, order: BestOrder) => void;
		disabled?: boolean;
		/** Hover text, e.g. that the setting is the project's. */
		title?: string;
		/** Without an outline, among a page header's quieter controls. */
		quiet?: boolean;
		/** A trophy in place of the words "Best by", for a toolbar with more in it (a group's). */
		trophy?: boolean;
		/** Which edge of the button the menu lines up with. */
		align?: 'start' | 'end';
	}

	let {
		key,
		order,
		keys,
		onchange,
		disabled = false,
		title,
		quiet = false,
		trophy = false,
		align = 'start'
	}: Props = $props();

	let query = $state('');
	const matches = $derived(
		query.trim() ? keys.filter((k) => k.toLowerCase().includes(query.trim().toLowerCase())) : keys
	);
	const SHOWN = 200;
</script>

<DropdownMenu.Root onOpenChange={() => (query = '')}>
	<DropdownMenu.Trigger {disabled}>
		{#snippet child({ props })}
			<Button
				{...props}
				variant={quiet ? 'ghost' : 'outline'}
				class="gap-1.5 pr-2 font-normal text-muted-foreground"
				title={`Best by ${bestWords(key, order)}${title ? ` (${title})` : ''}`}
			>
				{#if trophy}
					<TrophyIcon />
					<span class="sr-only">Best by</span>
				{:else}
					Best by
				{/if}
				<span class="max-w-48 truncate font-medium text-foreground">{bestLabel(key, order)}</span>
				<ChevronDownIcon class="text-muted-foreground" />
			</Button>
		{/snippet}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content {align} class="w-64">
		<DropdownMenu.Group>
			<DropdownMenu.Label>Best is the</DropdownMenu.Label>
			<DropdownMenu.RadioGroup
				value={order ?? ''}
				onValueChange={(v) => key && onchange(key, v as BestOrder)}
			>
				<DropdownMenu.RadioItem value="asc" disabled={!key}>Lowest</DropdownMenu.RadioItem>
				<DropdownMenu.RadioItem value="desc" disabled={!key}>Highest</DropdownMenu.RadioItem>
			</DropdownMenu.RadioGroup>
		</DropdownMenu.Group>
		<DropdownMenu.Separator />
		<DropdownMenu.Group>
			<DropdownMenu.Label>Summary column</DropdownMenu.Label>
			<!-- Typing here filters the columns; keys go to the input, not the menu's typeahead. -->
			<input
				class="mx-1 mb-1 h-7 w-[calc(100%-0.5rem)] rounded-md border border-input bg-background px-2 text-[13px] outline-none placeholder:text-faint focus-visible:border-ring"
				placeholder="Find a column"
				aria-label="Find a summary column"
				bind:value={query}
				onkeydown={(e) => e.stopPropagation()}
			/>
			<div class="max-h-64 overflow-y-auto">
				<DropdownMenu.RadioGroup
					value={key ?? ''}
					onValueChange={(v) => onchange(v, order ?? 'asc')}
				>
					{#each matches.slice(0, SHOWN) as k (k)}
						<DropdownMenu.RadioItem value={k} class="font-mono text-[12.5px]" closeOnSelect>
							<span class="truncate">{metricLabel(k)}</span>
						</DropdownMenu.RadioItem>
					{:else}
						<p class="px-1.5 py-1 text-xs text-muted-foreground">No matching columns</p>
					{/each}
				</DropdownMenu.RadioGroup>
			</div>
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>
