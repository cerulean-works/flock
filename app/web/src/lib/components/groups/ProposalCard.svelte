<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import TrophyIcon from '@lucide/svelte/icons/trophy';
	import XIcon from '@lucide/svelte/icons/x';
	import { valueLabel } from '#lib/chart/grouping.ts';
	import { Checkbox } from '#lib/components/ui/checkbox/index.js';
	import { displayKey, metricLabel, type Proposal } from '#lib/groups.ts';
	import { count, formatNumber } from '#lib/utils.ts';
	import { runHref } from '#lib/workspace/url.ts';
	import {
		axisSummary,
		cellTint,
		layoutGrid,
		orderedAxes,
		repeatText,
		strongTint
	} from './propose.ts';

	/**
	 * One proposed group, as the assistant's message shows it: its name and why, its axes and
	 * their values, how its runs cover those axes (a strip for one, a heatmap for two, each cell
	 * tinted by its best value), its best run and its repeats. The latest proposals can be
	 * renamed, left out of the create or removed; earlier ones are read only.
	 */

	interface Props {
		entity: string;
		project: string;
		proposal: Proposal;
		/** The hue the group will be saved with. */
		color: string;
		/** The latest set's cards can be changed; earlier ones are history. */
		live: boolean;
		included?: boolean;
		name?: string;
		onremove?: () => void;
		/** While and after creating it. */
		status?: 'creating' | 'done' | 'failed' | null;
		failure?: string | null;
	}

	let {
		entity,
		project,
		proposal: p,
		color,
		live,
		included = $bindable(true),
		name = $bindable(p.name),
		onremove,
		status = null,
		failure = null
	}: Props = $props();

	const layout = $derived(p.grid ? layoutGrid(p.grid, p.best_order, p.best?.run ?? null) : null);
	const two = $derived(!!layout && layout.ys[0] !== null);
	const repeats = $derived(repeatText(p));
	/** Axes beyond this many wait behind "+N more keys vary". */
	const MAX_AXES = 4;
	const axes = $derived(orderedAxes(p));
	let allAxes = $state(false);
	const off = $derived(live && !included);

	const cellTitle = (x: unknown, y: unknown, runs: number, best: number | null) =>
		[
			`${displayKey(p.grid!.x)} = ${valueLabel(x)}`,
			two ? `${displayKey(p.grid!.y!)} = ${valueLabel(y)}` : null,
			count(runs, 'run'),
			best !== null && p.best_key ? `best ${metricLabel(p.best_key)} ${formatNumber(best)}` : null
		]
			.filter(Boolean)
			.join(' · ');
</script>

<article
	class={[
		'relative overflow-hidden rounded-xl border bg-card transition-[opacity,border-color]',
		off ? 'border-dashed border-divider opacity-55' : 'border-divider',
		status === 'failed' && 'border-destructive/50'
	]}
	style:--hue={color}
	aria-label={name}
>
	<!-- The group's color, as the sidebar will show it. -->
	<span class="absolute inset-y-0 left-0 w-[3px]" style:background={color} aria-hidden="true"
	></span>
	<div class="grid gap-3 py-3.5 pr-3.5 pl-5">
		<header class="flex items-start gap-2.5">
			<span
				class="mt-[5px] size-3 flex-none rounded-[3px]"
				style:background={color}
				aria-hidden="true"
			></span>
			<div class="min-w-0 flex-1">
				{#if live && status !== 'done'}
					<label>
						<span class="sr-only">Group name</span>
						<input
							bind:value={name}
							class="-mx-1 w-full min-w-0 truncate rounded px-1 text-[15px] leading-6 font-medium outline-none hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50"
							maxlength={128}
							disabled={status === 'creating'}
						/>
					</label>
				{:else}
					<h3 class="truncate text-[15px] leading-6 font-medium">{name}</h3>
				{/if}
				<p class="mt-0.5 text-[13px] leading-[1.45] text-pretty text-muted-foreground">{p.why}</p>
			</div>
			<span
				class="mt-0.5 flex-none rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground tabular-nums"
			>
				{count(p.runs.length, 'run')}
			</span>
			{#if status === 'creating'}
				<LoaderCircleIcon class="mt-1 size-4 flex-none animate-spin text-muted-foreground" />
			{:else if status === 'done'}
				<span class="mt-0.5 flex items-center gap-1 text-xs text-state-finished">
					<CheckIcon class="size-3.5" />Created
				</span>
			{:else if live}
				<label
					class="mt-0.5 flex flex-none cursor-pointer items-center gap-1.5 rounded-md px-1 text-xs text-muted-foreground select-none hover:text-foreground"
					title={included ? 'Leave this group out' : 'Create this group'}
				>
					<Checkbox bind:checked={included} aria-label="Create {name}" />
					Create
				</label>
				{#if onremove}
					<button
						type="button"
						class="-mt-0.5 -mr-1 flex size-7 flex-none items-center justify-center rounded-md text-faint outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
						title="Don’t group these runs"
						aria-label="Don’t group {name}"
						onclick={onremove}
					>
						<XIcon class="size-4" />
					</button>
				{/if}
			{/if}
		</header>

		{#if axes.length}
			<dl class="grid gap-1.5">
				{#each axes.slice(0, allAxes ? axes.length : MAX_AXES) as axis (axis)}
					{@const s = axisSummary(p.values[axis] ?? [])}
					<div class="flex min-w-0 items-center gap-2">
						<dt
							class="w-28 flex-none truncate font-mono text-[12px] text-muted-foreground"
							title={displayKey(axis)}
						>
							{displayKey(axis)}
						</dt>
						<dd class="flex min-w-0 flex-wrap items-center gap-1">
							{#if s.kind === 'chips'}
								{#each s.values as v, i (i)}
									<span
										class="rounded-md bg-[color-mix(in_oklab,var(--hue)_12%,transparent)] px-1.5 py-px font-mono text-[12px]"
									>
										{valueLabel(v)}
									</span>
								{/each}
							{:else}
								<!-- Many numbers (a random search): their range, and where each falls. -->
								<span class="font-mono text-[12px] tabular-nums">{formatNumber(s.lo)}</span>
								<span
									class="relative mx-1 h-3.5 w-36 rounded-full bg-[color-mix(in_oklab,var(--hue)_8%,transparent)]"
									title="{s.n} values{s.log ? ', log scale' : ''}"
									aria-hidden="true"
								>
									{#each s.at as t, i (i)}
										<span
											class="absolute top-1/2 size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full opacity-70"
											style:left="{4 + t * 92}%"
											style:background={color}
										></span>
									{/each}
								</span>
								<span class="font-mono text-[12px] tabular-nums">{formatNumber(s.hi)}</span>
								<span class="ml-1 text-[12px] text-faint">
									{s.n} values{s.log ? ' · log' : ''}
								</span>
							{/if}
						</dd>
					</div>
				{/each}
			</dl>
			{#if axes.length > MAX_AXES}
				<button
					type="button"
					class="-mt-1.5 justify-self-start rounded px-0.5 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
					onclick={() => (allAxes = !allAxes)}
				>
					{allAxes ? 'Show fewer' : `+${axes.length - MAX_AXES} more keys vary`}
				</button>
			{/if}
		{/if}

		{#if layout && p.grid}
			<!-- Coverage: which values (pairs) have runs, tinted by how good each one's best is. -->
			<figure class="grid gap-1.5">
				<div class="overflow-x-auto pb-0.5">
					<table class="border-separate border-spacing-[3px] text-[11px] tabular-nums">
						{#if two}
							<thead>
								<tr>
									<th></th>
									<th
										colspan={layout.xs.length}
										class="pb-0.5 text-left font-mono font-normal text-faint"
									>
										{displayKey(p.grid.x)} →
									</th>
								</tr>
								<tr>
									<th class="pr-1.5 text-right font-mono font-normal text-faint">
										{displayKey(p.grid.y!)} ↓
									</th>
									{#each layout.xs as x, i (i)}
										<th
											class="max-w-20 min-w-11 truncate px-1 text-center font-mono font-normal text-muted-foreground"
										>
											{valueLabel(x)}
										</th>
									{/each}
								</tr>
							</thead>
						{/if}
						<tbody>
							{#each layout.rows as row, r (r)}
								<tr>
									{#if two}
										<th
											class="max-w-28 truncate pr-1.5 text-right font-mono font-normal text-muted-foreground"
										>
											{valueLabel(layout.ys[r])}
										</th>
									{/if}
									{#each row as cell, c (c)}
										{#if cell}
											<td
												class={[
													'h-9 min-w-11 rounded-md px-1.5 text-center align-middle',
													cell.isBest && 'outline-2 -outline-offset-1',
													strongTint(cell.strength) && 'font-medium'
												]}
												style:background={off ? 'var(--muted)' : cellTint(color, cell.strength)}
												style:outline-color={cell.isBest ? color : undefined}
												title={cellTitle(cell.x, cell.y, cell.runs, cell.best)}
											>
												{#if two}
													{cell.runs}
												{:else}
													<div class="max-w-24 truncate font-mono text-[11px]">
														{valueLabel(cell.x)}
													</div>
													<div class="text-[10px] text-muted-foreground">
														{count(cell.runs, 'run')}
													</div>
												{/if}
											</td>
										{:else}
											<td
												class="h-9 min-w-11 rounded-md border border-dashed border-divider text-center text-faint"
												title="No runs here"
											>
												{#if !two}
													<div class="font-mono">{valueLabel(layout.xs[c])}</div>
												{/if}
												–
											</td>
										{/if}
									{/each}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<figcaption
					class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground"
				>
					{#if two}
						<span>Cells: runs per pair</span>
					{/if}
					{#if layout.possible > layout.covered}
						<span>{layout.covered} of {layout.possible} combinations run</span>
					{/if}
					{#if p.best_key && p.best_order}
						<span class="flex items-center gap-1.5">
							<span>{metricLabel(p.best_key)}: worse</span>
							<span
								class="h-1.5 w-10 rounded-full"
								style:background="linear-gradient(to right, {cellTint(color, 0)}, {cellTint(
									color,
									1
								)})"
								aria-hidden="true"
							></span>
							<span>better</span>
						</span>
					{/if}
				</figcaption>
			</figure>
		{/if}

		{#if p.best || repeats}
			<div
				class="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-divider pt-2.5 text-[13px]"
			>
				{#if p.best}
					<span class="flex min-w-0 items-center gap-1.5">
						<TrophyIcon class="size-3.5 flex-none" style="color: {color}" />
						<span class="text-muted-foreground">Best</span>
						<a
							class="truncate font-medium hover:underline"
							href={runHref(entity, project, p.best.run)}
							target="_blank"
							rel="noopener"
						>
							{p.best.name}
						</a>
						{#if p.best_key}
							<span class="text-muted-foreground">
								{metricLabel(p.best_key)}
								<span class="font-medium text-foreground tabular-nums">
									{formatNumber(p.best.value)}
								</span>
							</span>
							{#if p.best.mean != null && (p.best.n ?? 0) > 1}
								<span
									class="text-muted-foreground"
									title="Groups are ranked by the mean over each configuration's repeats"
								>
									· mean of {p.best.n}
									<span class="text-foreground tabular-nums">{formatNumber(p.best.mean)}</span
									>{#if p.best.std != null}&nbsp;± {formatNumber(p.best.std)}{/if}
								</span>
							{/if}
						{/if}
					</span>
				{/if}
				{#if repeats}
					<span class="text-muted-foreground">{repeats}, averaged</span>
				{/if}
			</div>
		{/if}

		{#if status === 'failed' && failure}
			<p class="text-[13px] text-destructive" role="alert">Not created: {failure}</p>
		{/if}
	</div>
</article>
