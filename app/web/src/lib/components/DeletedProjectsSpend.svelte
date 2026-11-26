<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import CalendarIcon from '@lucide/svelte/icons/calendar';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import MetaItem from '#lib/components/MetaItem.svelte';
	import MetaRow from '#lib/components/MetaRow.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { deletedProjectName, deletedTotals, monthSpan } from '#lib/budget.ts';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { DeletedProject } from '#lib/types.ts';
	import { count, dateTime, hours, money } from '#lib/utils.ts';

	/** Admins only: the spend of deleted projects, which they can leave out of the budget. */
	let { projects, currency }: { projects: DeletedProject[]; currency: string } = $props();

	const totals = $derived(deletedTotals(projects));
	const fmt = (v: number) => money(v, currency);
	const changing = new Mutation<{ deleted: DeletedProject }>({
		toastErrors: true,
		success: ({ deleted }) =>
			deleted.excluded
				? `${deletedProjectName(deleted)} left out of the budget`
				: `${deletedProjectName(deleted)} counted in the budget again`
	});
</script>

<p class="mt-2 text-muted-foreground">
	Deleted projects still count toward the budget of the months they ran. Leave one out to stop
	counting it, or count it again later.
</p>

{#if projects.length}
	<MetaRow class="mt-4">
		<span><span class="text-foreground tabular-nums">{fmt(totals.counted)}</span> counted</span>
		<span><span class="text-foreground tabular-nums">{fmt(totals.excluded)}</span> left out</span>
	</MetaRow>
	<ul class="mt-2 border-t border-divider">
		{#each projects as p (p.id)}
			<li class="flex min-h-14 flex-wrap items-center gap-x-3 gap-y-1 border-b border-divider py-2">
				<div class="min-w-0 flex-1">
					<p class={['truncate font-medium', p.excluded && 'text-muted-foreground line-through']}>
						{deletedProjectName(p)}
					</p>
					<MetaRow class="mt-0.5">
						<MetaItem icon={CalendarIcon}>{monthSpan(p.months)}</MetaItem>
						<MetaItem icon={ClockIcon}>{hours(p.gpu_hours)} GPU hours</MetaItem>
						<MetaItem icon={ActivityIcon}>{count(p.runs, 'run')}</MetaItem>
						{#if p.deleted_at}
							<MetaItem icon={Trash2Icon}>
								Deleted {dateTime(p.deleted_at)}
							</MetaItem>
						{/if}
					</MetaRow>
				</div>
				{#if p.excluded}
					<Badge
						variant="secondary"
						class="text-muted-foreground"
						{@attach hint(p.excluded_by ? `Left out by ${p.excluded_by.name}` : undefined)}
					>
						Left out
					</Badge>
				{/if}
				<span class="w-28 text-right tabular-nums">{fmt(p.cost)}</span>
				<form method="POST" action="?/exclude" use:enhance={changing.enhance}>
					<input type="hidden" name="deleted" value={p.id} />
					<input type="hidden" name="excluded" value={p.excluded ? 'false' : 'true'} />
					<Button
						type="submit"
						variant="outline"
						size="sm"
						class="w-28"
						disabled={changing.pending}
						aria-label="{p.excluded ? 'Count' : 'Leave out'} {deletedProjectName(p)}"
					>
						{p.excluded ? 'Count again' : 'Leave out'}
					</Button>
				</form>
			</li>
		{/each}
	</ul>
{:else}
	<p class="mt-4 border-y border-divider py-3 text-[13px] text-muted-foreground">
		No deleted project used GPU time.
	</p>
{/if}
