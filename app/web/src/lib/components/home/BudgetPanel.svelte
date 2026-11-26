<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { Budget } from '#lib/types.ts';
	import { count, gpus, hours, money, monthName, shiftMonth } from '#lib/utils.ts';
	import { orgHref, projectHref, runHref, withParam } from '#lib/workspace/url.ts';

	interface Props {
		budget: Budget;
		/** The current UTC month: there's nothing to show after it. */
		thisMonth: string;
		/** Admins set the budget and prices, and manage deleted projects. */
		admin: boolean;
	}

	let { budget, thisMonth, admin }: Props = $props();

	/** The breakdown lists the costliest projects and runs only. */
	const TOP_PROJECTS = 5;
	const TOP_RUNS = 3;

	const fmt = $derived((v: number) => money(v, budget.currency));

	const used = $derived(budget.budget ? budget.spent / budget.budget : 0);
	const over = $derived(budget.remaining != null && budget.remaining < 0);
	// Static class strings, so Tailwind sees each one.
	const tone = $derived(
		over || used >= 1 ? 'bg-state-failed' : used >= 0.8 ? 'bg-state-preempting' : 'bg-link'
	);
	const maxCost = $derived(Math.max(...budget.projects.map((p) => p.cost), 0));
	const current = $derived(budget.month === thisMonth);
	let expanded = $state(false);

	const monthHref = (month: string) =>
		withParam(page.url, 'month', month === thisMonth ? null : month);
</script>

<!-- A quiet rail beside the feed: the month's figure, one bar, then where it went. On narrow
     windows it folds into one line above the feed, opened on request. -->
<section aria-labelledby="budget-title" class="grid grid-cols-1 gap-5 text-[13px]">
	<div class="flex h-7 items-center gap-1">
		<!-- The whole workspace's, whatever the feed beside it shows. -->
		<h2 id="budget-title" class="min-w-0 flex-1 truncate font-medium text-muted-foreground">
			Budget · {page.data.shell?.orgName ?? budget.org}
		</h2>
		<Button
			variant="quiet"
			size="icon-bar"
			href={monthHref(shiftMonth(budget.month, -1))}
			data-sveltekit-noscroll
			aria-label="Previous month"
			{@attach hint('Previous month')}
		>
			<ChevronLeftIcon />
		</Button>
		<span class="w-26 text-center text-muted-foreground tabular-nums"
			>{monthName(budget.month)}</span
		>
		<Button
			variant="quiet"
			size="icon-bar"
			class={current ? 'pointer-events-none opacity-40' : undefined}
			href={current ? undefined : monthHref(shiftMonth(budget.month, 1))}
			data-sveltekit-noscroll
			aria-label="Next month"
			{@attach hint('Next month')}
			aria-disabled={current}
		>
			<ChevronRightIcon />
		</Button>
	</div>

	<button
		type="button"
		class="-mt-3 flex items-center gap-1.5 justify-self-start rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 xl:hidden"
		aria-expanded={expanded}
		onclick={() => (expanded = !expanded)}
	>
		{#if budget.remaining != null && budget.budget != null}
			<span class={['text-foreground tabular-nums', over && 'text-destructive']}
				>{fmt(Math.abs(budget.remaining))}</span
			>
			{over ? 'over' : 'left'} of {fmt(budget.budget)}
		{:else}
			<span class="text-foreground tabular-nums">{fmt(budget.spent)}</span> spent
		{/if}
		<ChevronDownIcon class={['size-3.5 transition-transform', expanded && 'rotate-180']} />
	</button>

	<div class={['grid grid-cols-1 gap-6', !expanded && 'hidden xl:grid']}>
		<div class="grid gap-2">
			{#if budget.budget != null && budget.remaining != null}
				<p class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
					<span
						class={[
							'text-[24px] leading-none tracking-[-0.02em] tabular-nums',
							over && 'text-destructive'
						]}
					>
						{fmt(Math.abs(budget.remaining))}
					</span>
					<span class="whitespace-nowrap text-muted-foreground">
						{over ? 'over' : 'left'} of {fmt(budget.budget)}
					</span>
				</p>
				<div
					class="h-1 overflow-hidden rounded-full bg-muted"
					role="meter"
					aria-label="Budget used"
					aria-valuemin={0}
					aria-valuemax={100}
					aria-valuenow={Math.round(used * 100)}
				>
					<div
						class={['h-full rounded-full', tone]}
						style:width="{Math.min(100, used * 100)}%"
					></div>
				</div>
				<p class="text-muted-foreground tabular-nums">
					{fmt(budget.spent)} spent · {hours(budget.gpu_hours)} GPU hours
				</p>
			{:else}
				<p class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
					<span class="text-[24px] leading-none tracking-[-0.02em] tabular-nums">
						{fmt(budget.spent)}
					</span>
					<span class="text-muted-foreground">spent</span>
				</p>
				<p class="text-muted-foreground tabular-nums">
					{hours(budget.gpu_hours)} GPU hours · No monthly budget{#if admin}.
						<a class="text-link hover:underline" href={orgHref(budget.org, 'budget')}>Set one</a
						>{/if}
				</p>
			{/if}
			{#if budget.excluded.projects}
				<p class="text-muted-foreground">
					Leaves out {fmt(budget.excluded.cost)} of {count(
						budget.excluded.projects,
						'deleted project'
					)}.
					{#if admin}<a
							class="text-link hover:underline"
							href={orgHref(budget.org, 'deleted-projects')}>Manage</a
						>{/if}
				</p>
			{/if}
		</div>

		<div>
			<h3 class="text-xs font-medium text-muted-foreground">By project</h3>
			<ul class="mt-1">
				{#each budget.projects.slice(0, TOP_PROJECTS) as p (p.project)}
					<li
						class="py-1.5"
						{@attach hint(`${hours(p.gpu_hours)} GPU hours, ${count(p.runs, 'run')}`)}
					>
						<div class="flex items-baseline gap-2">
							{#if p.project}
								<a
									class="min-w-0 flex-1 truncate hover:text-link"
									href={projectHref(budget.org, p.project)}
								>
									{p.project}
								</a>
							{:else if admin}
								<a
									class="min-w-0 flex-1 truncate text-muted-foreground hover:text-link"
									href={orgHref(budget.org, 'deleted-projects')}
								>
									Deleted projects
								</a>
							{:else}
								<span class="min-w-0 flex-1 truncate text-muted-foreground">Deleted projects</span>
							{/if}
							<span class="text-muted-foreground tabular-nums">{fmt(p.cost)}</span>
						</div>
						<div class="mt-1 h-0.5 rounded-full bg-muted">
							<div
								class="h-full rounded-full bg-link/60"
								style:width="{maxCost ? (p.cost / maxCost) * 100 : 0}%"
							></div>
						</div>
					</li>
				{:else}
					<li class="py-1.5 text-muted-foreground">No GPU time in {monthName(budget.month)}.</li>
				{/each}
			</ul>
			{#if budget.projects.length > TOP_PROJECTS}
				<p class="mt-1 text-xs text-faint">
					And {count(budget.projects.length - TOP_PROJECTS, 'more project')}
				</p>
			{/if}
		</div>

		{#if budget.top_runs.length}
			<div>
				<h3 class="text-xs font-medium text-muted-foreground">Top runs</h3>
				<ol class="mt-1">
					{#each budget.top_runs.slice(0, TOP_RUNS) as run (`${run.project}/${run.id}`)}
						{@const hardware = gpus(run.gpu_type, run.gpu_count)}
						<li class="flex items-baseline gap-2 py-1.5">
							<a
								class="min-w-0 flex-1 truncate hover:text-link"
								href={runHref(run.entity, run.project, run.id)}
								{@attach hint(
									`${run.project}${run.owner ? `, ${run.owner.name}` : ''}${hardware ? `, ${hardware}` : ''}, ${hours(run.gpu_hours)} GPU hours`
								)}
							>
								{run.display_name ?? run.id}
							</a>
							<span class="text-muted-foreground tabular-nums">{fmt(run.cost)}</span>
						</li>
					{/each}
				</ol>
			</div>
		{/if}
	</div>
</section>
