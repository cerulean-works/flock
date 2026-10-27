<script lang="ts">
	import { enhance } from '$app/forms';
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
	const detail = (p: DeletedProject) =>
		[
			monthSpan(p.months),
			`${hours(p.gpu_hours)} GPU h`,
			count(p.runs, 'run'),
			p.deleted_at && `deleted ${dateTime(p.deleted_at)}`
		]
			.filter(Boolean)
			.join(' · ');
	const changing = new Mutation<{ deleted: DeletedProject }>({
		toastErrors: true,
		success: ({ deleted }) =>
			deleted.excluded
				? `${deletedProjectName(deleted)} left out of the budget`
				: `${deletedProjectName(deleted)} counted in the budget again`
	});
</script>

<p class="mt-2 max-w-[62ch] text-muted-foreground">
	Deleting a project doesn’t give its spend back: its GPU hours still count in the budget of the
	months they ran. Leave a project out to stop counting them, in every month; you can count it again
	at any time.
</p>

{#if projects.length}
	<p class="mt-4 text-[13px] text-muted-foreground">
		{fmt(totals.counted)} counted · {fmt(totals.excluded)} left out
	</p>
	<ul class="mt-2 border-t border-divider">
		{#each projects as p (p.id)}
			<li class="flex min-h-14 flex-wrap items-center gap-x-3 gap-y-1 border-b border-divider py-2">
				<div class="min-w-0 flex-1">
					<p class={['truncate font-medium', p.excluded && 'text-muted-foreground line-through']}>
						{deletedProjectName(p)}
					</p>
					<p class="truncate text-[13px] text-muted-foreground">
						{detail(p)}
					</p>
				</div>
				{#if p.excluded}
					<Badge
						variant="secondary"
						class="text-muted-foreground"
						title={p.excluded_by ? `Left out by ${p.excluded_by.name}` : undefined}
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
