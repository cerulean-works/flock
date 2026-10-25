<script lang="ts">
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { formatNumber } from '#lib/utils.ts';

	interface Props {
		/** The first row shown, from 0. */
		offset: number;
		/** Rows on this page. */
		count: number;
		total: number;
		/** Rows per page, one of `sizes`. */
		size: number;
		sizes: readonly number[];
		/** What is paged, for screen readers and the size menu: "Rows". */
		label?: string;
		onoffset: (offset: number) => void;
		onsize: (size: number) => void;
		disabled?: boolean;
	}

	let {
		offset,
		count,
		total,
		size,
		sizes,
		label = 'Rows',
		onoffset,
		onsize,
		disabled = false
	}: Props = $props();

	let choosing = $state(false);
</script>

<!-- Paging by props, for tables that keep their page themselves (the runs table's Pager reads
     its workspace instead). -->
<nav class="flex h-8 items-center gap-1 text-[13px] tabular-nums" aria-label="{label} pages">
	<span>
		{#if count}{formatNumber(offset + 1)}–{formatNumber(offset + count)}{:else}0{/if}
	</span>
	<span class="mr-1 text-muted-foreground">of {formatNumber(total)}</span>
	<Button
		variant="ghost"
		size="icon-sm"
		disabled={disabled || offset <= 0}
		aria-label="Previous page"
		onclick={() => onoffset(Math.max(0, offset - size))}
	>
		<ChevronLeftIcon />
	</Button>
	<Button
		variant="ghost"
		size="icon-sm"
		disabled={disabled || offset + size >= total}
		aria-label="Next page"
		onclick={() => onoffset(offset + size)}
	>
		<ChevronRightIcon />
	</Button>
	<DropdownMenu.Root bind:open={choosing}>
		<DropdownMenu.Trigger>
			{#snippet child({ props })}
				<Button
					{...props}
					variant="ghost"
					size="sm"
					class="gap-1 px-2 font-normal"
					aria-label="{label} per page"
				>
					{size} per page
					<ChevronDownIcon class="text-muted-foreground" />
				</Button>
			{/snippet}
		</DropdownMenu.Trigger>
		<DropdownMenu.Content align="end" class="w-40">
			<DropdownMenu.Group>
				<DropdownMenu.Label>{label} per page</DropdownMenu.Label>
				<DropdownMenu.RadioGroup
					value={String(size)}
					onValueChange={(v) => {
						choosing = false;
						if (Number(v) !== size) onsize(Number(v));
					}}
				>
					{#each sizes as n (n)}
						<DropdownMenu.RadioItem value={String(n)}>{n}</DropdownMenu.RadioItem>
					{/each}
				</DropdownMenu.RadioGroup>
			</DropdownMenu.Group>
		</DropdownMenu.Content>
	</DropdownMenu.Root>
</nav>
