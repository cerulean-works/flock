<script lang="ts" module>
	/** A column of Compare's spread chart: one group's runs, its best standing out. */
	export interface BestDot {
		key: string;
		/** The best run, hovered with the table and the history chart. */
		run: string | null;
		label: string;
		color: string;
		/** The best value (the best configuration's mean when it has repeats). */
		value: number;
		/** Spread across the best configuration's repeats, when it has some. */
		std: number | null;
		/** Every member's value. */
		values: readonly number[];
	}
</script>

<script lang="ts">
	import { niceDomain, niceTicks } from '#lib/chart/scale.ts';
	import { count, formatNumber, formatTick } from '#lib/utils.ts';

	interface Props {
		dots: BestDot[];
		hovered: string | null;
		onhover: (run: string | null) => void;
	}

	let { dots, hovered, onhover }: Props = $props();

	/**
	 * The y range: the bests with their spread, and the runs' values short of
	 * outliers (past 1.5 interquartile ranges, a diverged run's loss), padded a little. Runs off
	 * it sit at its edge.
	 */
	const domain = $derived.by((): [number, number] => {
		let lo = Infinity;
		let hi = -Infinity;
		const take = (v: number) => {
			if (!Number.isFinite(v)) return;
			lo = Math.min(lo, v);
			hi = Math.max(hi, v);
		};
		for (const d of dots) {
			take(d.value - (d.std ?? 0));
			take(d.value + (d.std ?? 0));
		}
		const all = dots.flatMap((d) => d.values.filter(Number.isFinite)).sort((a, b) => a - b);
		if (all.length) {
			const q = (p: number) => all[Math.min(all.length - 1, Math.floor(p * all.length))];
			const iqr = q(0.75) - q(0.25);
			const [from, to] = [q(0.25) - 1.5 * iqr, q(0.75) + 1.5 * iqr];
			for (const v of all) if (v >= from && v <= to) take(v);
		}
		return niceDomain(lo, hi);
	});
	/** Runs off the range, which sit at its edge. */
	const off = $derived(
		dots.reduce((n, d) => n + d.values.filter((v) => v < domain[0] || v > domain[1]).length, 0)
	);
	const ticks = $derived(niceTicks(domain[0], domain[1], 5));
	/** Percent from the bottom. */
	const y = (v: number) =>
		Math.max(0, Math.min(100, ((v - domain[0]) / (domain[1] - domain[0])) * 100));
	/** A run's place across its column, spread so equal values don't hide each other. */
	const jitter = (i: number) => (((i * 0.6180339887) % 1) - 0.5) * 56;
	/** Names fit under the columns only when there are few of them. */
	const named = $derived(dots.length <= 10);
</script>

<!-- One column per group on the best-by metric's own axis: every run faint, the best (with its
     repeats' spread) solid. -->
<div class="flex min-h-0 flex-1 flex-col text-[12.5px] text-muted-foreground tabular-nums">
	<div class="relative min-h-0 flex-1">
		<div class="absolute top-1.5 bottom-0 left-0 w-10" aria-hidden="true">
			{#each ticks.values.filter((t) => t >= domain[0] && t <= domain[1]) as t (t)}
				<span class="absolute right-0 translate-y-1/2 whitespace-nowrap" style:bottom="{y(t)}%"
					>{formatTick(t, ticks.step, 'num')}</span
				>
			{/each}
		</div>
		<div class="absolute top-1.5 right-1.5 bottom-0 left-14">
			{#each ticks.values.filter((t) => t >= domain[0] && t <= domain[1]) as t (t)}
				<div class="absolute inset-x-0 h-px bg-divider" style:bottom="{y(t)}%"></div>
			{/each}
			<div class="absolute inset-0 flex">
				{#each dots as d (d.key)}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<div
						class={[
							'relative min-w-0 flex-1 transition-opacity',
							hovered != null && d.run != null && hovered !== d.run && 'opacity-35'
						]}
						onpointerenter={() => onhover(d.run)}
						onpointerleave={() => onhover(null)}
						title="{d.label}: best {formatNumber(d.value)}{d.std != null
							? ` ± ${formatNumber(d.std)}`
							: ''} · {count(d.values.length, 'run')}"
					>
						{#each d.values as v, i (i)}
							<span
								class="absolute size-1.5 -translate-x-1/2 translate-y-1/2 rounded-full opacity-45"
								style:left="calc(50% + {jitter(i + 1)}%)"
								style:bottom="{y(v)}%"
								style:background={d.color}
							></span>
						{/each}
						{#if d.std != null && d.std > 0}
							<span
								class="absolute left-1/2 w-0.5 -translate-x-1/2 rounded-full"
								style:bottom="{y(d.value - d.std)}%"
								style:height="{y(d.value + d.std) - y(d.value - d.std)}%"
								style:background={d.color}
							></span>
						{/if}
						<span
							class="absolute left-1/2 size-3 -translate-x-1/2 translate-y-1/2 rounded-full ring-2 ring-card"
							style:bottom="{y(d.value)}%"
							style:background={d.color}
						></span>
					</div>
				{/each}
			</div>
		</div>
	</div>
	<div class="mt-2.5 flex h-9 pr-1.5 pl-14 text-xs">
		{#each dots as d (d.key)}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class={[
					'flex min-w-0 flex-1 flex-col items-center px-1 leading-tight transition-opacity',
					hovered != null && d.run != null && hovered !== d.run && 'opacity-35'
				]}
				onpointerenter={() => onhover(d.run)}
				onpointerleave={() => onhover(null)}
				title={d.label}
			>
				{#if named}
					<span class="flex max-w-full min-w-0 items-center gap-1.5 text-foreground">
						<span class="size-2 flex-none rounded-[2px]" style:background={d.color}></span>
						<span class="truncate">{d.label}</span>
					</span>
					<span class="max-w-full truncate">{formatNumber(d.value)}</span>
				{:else}
					<span class="mt-1 h-2 w-full max-w-3 rounded-[2px]" style:background={d.color}></span>
				{/if}
			</div>
		{/each}
	</div>
	{#if off}
		<p class="pt-1 pl-14 text-[11px] text-faint">
			{count(off, 'run')} off the scale (past 1.5 interquartile ranges), drawn at its edge
		</p>
	{/if}
</div>
