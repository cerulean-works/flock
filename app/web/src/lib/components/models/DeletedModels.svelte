<script lang="ts">
	import BoxIcon from '@lucide/svelte/icons/box';
	import GitCommitVerticalIcon from '@lucide/svelte/icons/git-commit-vertical';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { DialogSubject } from '#lib/data/dialog-subject.svelte.ts';
	import { daysLeft } from '#lib/datasets.ts';
	import { TRASH_DAYS } from '#lib/models.ts';
	import type { ModelTrashEntry } from '#lib/types.ts';
	import { bytes, count, dateTime } from '#lib/utils.ts';
	import PurgeModelDialog from './PurgeModelDialog.svelte';
	import RestoreModelDialog from './RestoreModelDialog.svelte';

	/** Admins only: models and versions deleted from the Models tab, waiting in the trash (D109). */
	let { org, entries, now }: { org: string; entries: ModelTrashEntry[]; now: number } = $props();

	const restoring = new DialogSubject<ModelTrashEntry>();
	const purging = new DialogSubject<ModelTrashEntry>();

	const left = (entry: ModelTrashEntry) => {
		const days = daysLeft(entry.expires_at, now);
		return days ? `${count(days, 'day')} left` : 'Removed within a day';
	};
	const label = (entry: ModelTrashEntry) =>
		entry.kind === 'model' ? entry.model : `${entry.model}@${entry.number}`;
	const what = (entry: ModelTrashEntry) =>
		entry.kind === 'model' ? `Model with ${count(entry.versions, 'version')}` : 'Version';
</script>

<p class="mt-2 text-muted-foreground">
	Models and versions deleted from the Models tab stay here for {TRASH_DAYS} days, then they’re removed
	for good.
</p>

{#if entries.length}
	<table class="mt-4 w-full table-fixed border-collapse text-[13.5px]">
		<thead>
			<tr class="h-10 border-b text-left text-xs font-medium text-muted-foreground">
				<th class="px-2 font-medium">Name</th>
				<th class="hidden w-32 px-2 font-medium sm:table-cell">Project</th>
				<th class="w-44 px-2 sm:w-64"><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		<tbody>
			{#each entries as entry (`${entry.kind}:${entry.id}`)}
				<tr class="h-14 border-b border-divider">
					<td class="px-2 py-2">
						<p class="truncate font-mono text-[13px]" title={label(entry)}>{label(entry)}</p>
						<MetaRow class="mt-0.5 flex-nowrap gap-x-3">
							<MetaItem icon={entry.kind === 'model' ? BoxIcon : GitCommitVerticalIcon}>
								{what(entry)}
							</MetaItem>
							<MetaItem icon={HardDriveIcon} title="Size">{bytes(entry.bytes)}</MetaItem>
							<MetaItem icon={Trash2Icon} title="Deleted">
								{dateTime(entry.deleted_at)}{entry.deleted_by ? ` by ${entry.deleted_by.name}` : ''}
							</MetaItem>
						</MetaRow>
					</td>
					<td
						class="hidden truncate px-2 text-muted-foreground sm:table-cell"
						title={entry.project ?? undefined}
					>
						{entry.project ?? ''}
					</td>
					<td class="px-2">
						<div class="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
							{#if !entry.moved}
								<span class="text-[13px] text-muted-foreground">Still being moved</span>
							{:else}
								<span class="text-[13px] text-muted-foreground tabular-nums">{left(entry)}</span>
								<div class="flex gap-1">
									<Button variant="outline" size="sm" onclick={() => restoring.show(entry)}
										>Restore</Button
									>
									<Button
										variant="ghost"
										size="sm"
										class="text-muted-foreground hover:text-destructive"
										onclick={() => purging.show(entry)}
									>
										Delete now
									</Button>
								</div>
							{/if}
						</div>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
{:else}
	<p class="mt-4 text-[13px] text-muted-foreground">No deleted models.</p>
{/if}

<RestoreModelDialog bind:open={restoring.open} {org} entry={restoring.value} />
<PurgeModelDialog bind:open={purging.open} {org} entry={purging.value} />
