<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.js';
	import { TRASH_DAYS, daysLeft } from '#lib/datasets.ts';
	import type { TrashEntry } from '#lib/types.ts';
	import { bytes, count, dateTime } from '#lib/utils.ts';
	import PurgeDatasetDialog from './PurgeDatasetDialog.svelte';
	import RestoreDatasetDialog from './RestoreDatasetDialog.svelte';

	/** Admins only: datasets deleted from their page, waiting in the trash (D93). */
	let { org, entries, now }: { org: string; entries: TrashEntry[]; now: number } = $props();

	let restoring = $state<TrashEntry | null>(null);
	let purging = $state<TrashEntry | null>(null);

	const left = (entry: TrashEntry) => {
		const days = daysLeft(entry.expires_at, now);
		return days ? `${count(days, 'day')} left` : 'Removed within a day';
	};
</script>

<p class="mt-2 max-w-[62ch] text-muted-foreground">
	Datasets deleted from the Datasets tab stay here for {TRASH_DAYS} days, then they’re removed for good.
</p>

{#if entries.length}
	<ul class="mt-4 border-t border-divider">
		{#each entries as entry (entry.id)}
			<li class="flex min-h-14 flex-wrap items-center gap-x-3 gap-y-1 border-b border-divider py-2">
				<div class="min-w-0 flex-1">
					<p class="truncate font-mono text-[13px]" title={entry.path}>{entry.path}</p>
					<p class="truncate text-[13px] text-muted-foreground">
						{bytes(entry.bytes)} · deleted {dateTime(entry.deleted_at)}{entry.deleted_by
							? ` by ${entry.deleted_by.name}`
							: ''}
					</p>
				</div>
				{#if entry.moved_at === null}
					<span class="text-[13px] text-muted-foreground">Still being moved</span>
				{:else}
					<span class="text-[13px] text-muted-foreground tabular-nums">{left(entry)}</span>
					<div class="flex gap-1">
						<Button variant="outline" size="sm" onclick={() => (restoring = entry)}>Restore</Button>
						<Button
							variant="ghost"
							size="sm"
							class="text-muted-foreground hover:text-destructive"
							onclick={() => (purging = entry)}
						>
							Delete now
						</Button>
					</div>
				{/if}
			</li>
		{/each}
	</ul>
{:else}
	<p class="mt-4 text-[13px] text-muted-foreground">No deleted datasets.</p>
{/if}

<RestoreDatasetDialog
	bind:open={() => restoring !== null, (o) => (restoring = o ? restoring : null)}
	{org}
	entry={restoring}
/>
<PurgeDatasetDialog
	bind:open={() => purging !== null, (o) => (purging = o ? purging : null)}
	{org}
	entry={purging}
/>
