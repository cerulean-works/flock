<script lang="ts">
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import XIcon from '@lucide/svelte/icons/x';
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import Avatar from '#lib/components/Avatar.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { splitWarnings, warningText } from '#lib/budget.ts';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import type { Budget } from '#lib/types.ts';
	import { count, gpus, hours, money, monthName, shiftMonth } from '#lib/utils.ts';
	import { orgHref, projectHref, withParam } from '#lib/workspace/url.ts';

	interface Props {
		budget: Budget;
		/** The current UTC month: there's nothing to show after it. */
		thisMonth: string;
		/** Admins set the budget and prices, dismiss warnings and manage deleted projects. */
		admin: boolean;
	}

	let { budget, thisMonth, admin }: Props = $props();

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

	// Warnings an admin dismissed stay out of sight for everyone, until they change.
	const warnings = $derived(splitWarnings(budget.warnings));
	let showDismissed = $state(false);
	const dismissing = new Mutation({ toastErrors: true, success: () => 'Warning dismissed' });
	const restoring = new Mutation({ toastErrors: true });

	const monthHref = (month: string) =>
		withParam(page.url, 'month', month === thisMonth ? null : month);
</script>

<section aria-labelledby="budget-title" class="grid gap-6">
	<!-- The same 40px band as the activity's heading beside it. -->
	<div class="flex h-10 items-center gap-1">
		<h2 id="budget-title" class="flex-1 font-serif text-[22px] tracking-[-0.01em]">Budget</h2>
		<Button
			variant="ghost"
			size="icon-sm"
			class="text-muted-foreground"
			href={monthHref(shiftMonth(budget.month, -1))}
			data-sveltekit-noscroll
			aria-label="Previous month"
			title="Previous month"
		>
			<ChevronLeftIcon />
		</Button>
		<span class="w-28 text-center text-[13px] text-muted-foreground tabular-nums">
			{monthName(budget.month)}
		</span>
		<Button
			variant="ghost"
			size="icon-sm"
			class={['text-muted-foreground', current && 'pointer-events-none opacity-40']}
			href={current ? undefined : monthHref(shiftMonth(budget.month, 1))}
			data-sveltekit-noscroll
			aria-label="Next month"
			title="Next month"
			aria-disabled={current}
		>
			<ChevronRightIcon />
		</Button>
	</div>

	<div class="grid gap-2">
		{#if budget.budget != null && budget.remaining != null}
			<!-- The caption stays whole: beside the amount when it fits, under it when it doesn't. -->
			<p class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
				<span
					class={[
						'font-serif text-[34px] leading-none tracking-[-0.02em]',
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
				class="h-1.5 overflow-hidden rounded-full bg-muted"
				role="meter"
				aria-label="Budget used"
				aria-valuemin={0}
				aria-valuemax={100}
				aria-valuenow={Math.round(used * 100)}
			>
				<div class={['h-full rounded-full', tone]} style:width="{Math.min(100, used * 100)}%"></div>
			</div>
			<p class="text-[13px] text-muted-foreground">
				{fmt(budget.spent)} spent · {hours(budget.gpu_hours)} GPU hours
			</p>
		{:else}
			<p class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
				<span class="font-serif text-[34px] leading-none tracking-[-0.02em]">
					{fmt(budget.spent)}
				</span>
				<span class="text-muted-foreground">spent</span>
			</p>
			<p class="text-[13px] text-muted-foreground">
				{hours(budget.gpu_hours)} GPU hours · no monthly budget
				{#if admin}· <a class="text-link hover:underline" href={orgHref(budget.org, 'budget')}
						>Set one</a
					>{/if}
			</p>
		{/if}
	</div>

	{#if budget.excluded.projects}
		<p class="-mt-4 text-[13px] text-muted-foreground">
			Leaves out {fmt(budget.excluded.cost)} of {count(
				budget.excluded.projects,
				'deleted project'
			)}.
			{#if admin}<a class="text-link hover:underline" href={orgHref(budget.org, 'deleted-projects')}
					>Manage</a
				>{/if}
		</p>
	{/if}

	{#if warnings.shown.length || (admin && warnings.dismissed.length)}
		<div class="grid gap-1.5 text-[13px]">
			{#if warnings.shown.length}
				<ul class="grid gap-1.5 border-l-2 border-state-preempting pl-3">
					{#each warnings.shown as w (w.id)}
						<li class="flex items-start gap-1">
							<p class={['flex-1', w.kind === 'unknown_gpu' && 'text-muted-foreground']}>
								{warningText(w)}
								{#if admin && w.kind === 'unpriced'}<a
										class="text-link hover:underline"
										href={orgHref(budget.org, 'budget')}>Set prices</a
									>{/if}
							</p>
							{#if admin}
								<form method="POST" action="?/dismissWarning" use:enhance={dismissing.enhance}>
									<input type="hidden" name="org" value={budget.org} />
									<input type="hidden" name="warning" value={w.id} />
									<input type="hidden" name="fingerprint" value={w.fingerprint} />
									<Button
										type="submit"
										variant="ghost"
										size="icon-sm"
										class="-my-1 size-6 text-muted-foreground"
										aria-label="Dismiss for everyone"
										title="Dismiss for everyone"
										disabled={dismissing.pending}
									>
										<XIcon />
									</Button>
								</form>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
			{#if admin && warnings.dismissed.length}
				<Button
					variant="ghost"
					size="sm"
					class="h-auto justify-self-start px-0 py-0 text-xs text-muted-foreground hover:bg-transparent"
					aria-expanded={showDismissed}
					onclick={() => (showDismissed = !showDismissed)}
				>
					{showDismissed ? 'Hide' : 'Show'}
					{count(warnings.dismissed.length, 'dismissed warning')}
				</Button>
				{#if showDismissed}
					<ul class="grid gap-1.5 border-l-2 border-divider pl-3 text-muted-foreground">
						{#each warnings.dismissed as w (w.id)}
							<li class="flex items-start gap-2">
								<p class="flex-1">
									{warningText(w)}
									{#if w.dismissed_by}<span class="text-xs">
											Dismissed by {w.dismissed_by.name}.</span
										>{/if}
								</p>
								<form method="POST" action="?/restoreWarning" use:enhance={restoring.enhance}>
									<input type="hidden" name="org" value={budget.org} />
									<input type="hidden" name="warning" value={w.id} />
									<Button
										type="submit"
										variant="ghost"
										size="sm"
										class="-my-1 h-6 px-1.5 text-xs"
										disabled={restoring.pending}
									>
										Show again
									</Button>
								</form>
							</li>
						{/each}
					</ul>
				{/if}
			{/if}
		</div>
	{/if}

	<!-- Narrow windows show the totals first, and the breakdown on request. -->
	<Button
		variant="ghost"
		size="sm"
		class="-mt-3 justify-self-start px-0 text-muted-foreground hover:bg-transparent xl:hidden"
		aria-expanded={expanded}
		onclick={() => (expanded = !expanded)}
	>
		{expanded ? 'Hide breakdown' : 'Show breakdown'}
	</Button>
	<div class={['grid gap-6', !expanded && 'hidden xl:grid']}>
		<div>
			<h3 class="text-xs font-medium text-muted-foreground">By project</h3>
			<ul class="mt-2">
				{#each budget.projects as p (p.project)}
					<li class="py-1.5">
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
									title="Manage the spend of deleted projects"
								>
									Deleted projects
								</a>
							{:else}
								<span class="min-w-0 flex-1 truncate text-muted-foreground">Deleted projects</span>
							{/if}
							<span class="tabular-nums">{fmt(p.cost)}</span>
						</div>
						<div class="mt-1 h-1 rounded-full bg-muted">
							<div
								class="h-full rounded-full bg-link/70"
								style:width="{maxCost ? (p.cost / maxCost) * 100 : 0}%"
							></div>
						</div>
						<p class="mt-0.5 text-xs text-muted-foreground">
							{hours(p.gpu_hours)} GPU h · {count(p.runs, 'run')}
						</p>
					</li>
				{:else}
					<li class="py-1.5 text-[13px] text-muted-foreground">
						No GPU time in {monthName(budget.month)}.
					</li>
				{/each}
			</ul>
		</div>

		{#if budget.top_runs.length}
			<div>
				<h3 class="text-xs font-medium text-muted-foreground">Most expensive runs</h3>
				<ol class="mt-1">
					{#each budget.top_runs as run (`${run.project}/${run.id}`)}
						{@const hardware = gpus(run.gpu_type, run.gpu_count)}
						<li class="flex items-center gap-2.5 border-b border-divider py-2 last:border-0">
							<span title={run.owner?.name}>
								<Avatar
									name={run.owner?.name ?? '?'}
									src={run.owner?.avatar_url}
									class="size-6 text-[10px]"
								/>
							</span>
							<div class="min-w-0 flex-1">
								<a
									class="block truncate hover:text-link"
									href={projectHref(run.entity, run.project)}
								>
									{run.display_name ?? run.id}
								</a>
								<p class="truncate text-xs text-muted-foreground">
									{run.project}{#if hardware}<span class="ml-1">· {hardware}</span>{/if}<span
										class="ml-1">· {hours(run.gpu_hours)} h</span
									>{#if run.owner}<span class="ml-1">· {run.owner.name}</span>{/if}
								</p>
							</div>
							<span class="flex-none tabular-nums">{fmt(run.cost)}</span>
						</li>
					{/each}
				</ol>
			</div>
		{/if}
	</div>
</section>
