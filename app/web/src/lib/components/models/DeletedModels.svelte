<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.js';
	import { daysLeft } from '#lib/datasets.ts';
	import { TRASH_DAYS } from '#lib/models.ts';
	import type { ModelTrashEntry } from '#lib/types.ts';
	import { bytes, count, dateTime } from '#lib/utils.ts';
	import PurgeModelDialog from './PurgeModelDialog.svelte';
	import RestoreModelDialog from './RestoreModelDialog.svelte';

	/** Admins only: models and versions deleted from the Models tab, waiting in the trash (D109). */
	let { org, entries, now }: { org: string; entries: ModelTrashEntry[]; now: number } = $props();

	let restoring = $state<ModelTrashEntry | null>(null);
	let purging = $state<ModelTrashEntry | null>(null);

	const left = (entry: ModelTrashEntry) => {
		const days = daysLeft(entry.expires_at, now);
		return days ? `${count(days, 'day')} left` : 'Removed within a day';
	};
	const label = (entry: ModelTrashEntry) =>
		entry.kind === 'model' ? entry.model : `${entry.model}@${entry.number}`;
	const what = (entry: ModelTrashEntry) =>
		entry.kind === 'model' ? `Model with ${count(entry.versions, 'version')}` : 'Version';
</script>

<p class="mt-2 max-w-[62ch] text-muted-foreground">
	Models and versions deleted from the Models tab stay here for {TRASH_DAYS} days, then they’re removed
	for good.
</p>

{#if entries.length}
	<ul class="mt-4 border-t border-divider">
		{#each entries as entry (`${entry.kind}:${entry.id}`)}
			<li class="flex min-h-14 flex-wrap items-center gap-x-3 gap-y-1 border-b border-divider py-2">
				<div class="min-w-0 flex-1">
					<p class="truncate font-mono text-[13px]" title={label(entry)}>{label(entry)}</p>
					<p class="truncate text-[13px] text-muted-foreground">
						{what(entry)} · {bytes(entry.bytes)} · deleted {dateTime(
							entry.deleted_at
						)}{entry.deleted_by ? ` by ${entry.deleted_by.name}` : ''}
					</p>
				</div>
				{#if !entry.moved}
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
	<p class="mt-4 text-[13px] text-muted-foreground">No deleted models.</p>
{/if}

<RestoreModelDialog
	bind:open={() => restoring !== null, (o) => (restoring = o ? restoring : null)}
	{org}
	entry={restoring}
/>
<PurgeModelDialog
	bind:open={() => purging !== null, (o) => (purging = o ? purging : null)}
	{org}
	entry={purging}
/>
