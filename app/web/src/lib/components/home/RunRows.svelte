<script lang="ts">
	import { untrack } from 'svelte';
	import { hint } from '#lib/hint.svelte.ts';
	import Avatar from '#lib/components/Avatar.svelte';
	import { runCurves, type Curve } from '#lib/home/curves.ts';
	import { mainMetric } from '#lib/home/pins.svelte.ts';
	import type { HomeRun } from '#lib/home/runs.ts';
	import { LIVE_STATES } from '#lib/types.ts';
	import { duration, formatNumber, relativeTime } from '#lib/utils.ts';
	import { runHref } from '#lib/workspace/url.ts';

	interface Props {
		runs: HomeRun[];
		now: number;
		/** Lead each row with who logged it. */
		who?: boolean;
		/** The viewer: their own rows say "You". */
		me?: number | null;
		/** The rows the live chart shows now (`project/id`): lit, as if hovered. */
		lit?: string[];
		/** Told the row under the pointer (`project/id`, null on leaving), for the live chart. */
		onhover?: (key: string | null) => void;
		/** Re-read live curves this often. */
		poll?: number;
	}

	let { runs, now, who = false, me = null, lit = [], onhover, poll = 30_000 }: Props = $props();

	const DOT: Record<string, string> = {
		running: 'bg-state-running',
		preempting: 'bg-state-preempting',
		finished: 'bg-state-finished',
		crashed: 'bg-state-failed',
		failed: 'bg-state-failed'
	};
	const ENDED: Record<string, string> = { failed: 'Failed', crashed: 'Crashed' };
	const keyOf = (r: HomeRun) => `${r.project}/${r.id}`;

	// A live row draws its main curve (its loss), read again while the tab is visible.
	let curves = $state<Record<string, { metric: string; curve: Curve } | null>>({});
	$effect(() => {
		const live = runs.filter((r) => LIVE_STATES.has(r.state));
		if (!live.length) return;
		const controller = new AbortController();
		const readAll = () => {
			for (const r of live) {
				const key = keyOf(r);
				const known = untrack(() => curves[key]?.metric);
				runCurves(r.org, r.project, r.id, known ? [known] : undefined, controller.signal)
					.then((series) => {
						const metric = known ?? mainMetric(Object.keys(series));
						curves[key] = metric && series[metric] ? { metric, curve: series[metric] } : null;
					})
					.catch(() => {});
			}
		};
		readAll();
		const id = setInterval(() => {
			if (document.visibilityState === 'visible') readAll();
		}, poll);
		return () => {
			clearInterval(id);
			controller.abort();
		};
	});

	/** One number of decimals down the column, so its right edge is straight (2.930 by 0.620);
	 * very small or large values keep the shared formatter. The full value is in the hint. */
	const even = (v: number) =>
		Number.isFinite(v) && Math.abs(v) >= 0.1 && Math.abs(v) < 1000 ? v.toFixed(3) : formatNumber(v);

	const lastOf = (c: Curve) => {
		for (let i = c.y.length - 1; i >= 0; i--) {
			const v = c.y[i];
			if (v != null && Number.isFinite(v)) return v;
		}
		return NaN;
	};
</script>

<!-- Runs as rows: a live one shows its latest value (its loss) and runtime; one that ended, how
     and when. The curves are the Live tile's to show. -->
<ul class="-mx-2 grid">
	{#each runs as r, i (keyOf(r))}
		{@const on = lit.includes(keyOf(r))}
		<!-- Lit rows side by side read as one block: the corners they share go square. -->
		{@const joinUp = on && i > 0 && lit.includes(keyOf(runs[i - 1]))}
		{@const joinDown = on && i < runs.length - 1 && lit.includes(keyOf(runs[i + 1]))}
		{@const c = curves[keyOf(r)]}
		{@const live = LIVE_STATES.has(r.state)}
		<li>
			<a
				href={runHref(r.org, r.project, r.id)}
				class={[
					'flex h-11 min-w-0 items-center gap-2.5 rounded-md px-2 text-[13px] transition-colors duration-300 hover:bg-muted/70',
					on && 'bg-muted/70',
					joinUp && 'rounded-t-none',
					joinDown && 'rounded-b-none'
				]}
				onpointerenter={() => onhover?.(keyOf(r))}
				onpointerleave={() => onhover?.(null)}
			>
				{#if who && r.owner}
					<span class="relative flex-none" {@attach hint(r.owner.name)}>
						<Avatar name={r.owner.name} src={r.owner.avatar_url} class="size-5 text-[9px]" />
						<span
							class={[
								'absolute -right-0.5 -bottom-0.5 size-2 rounded-full ring-2 ring-card',
								DOT[r.state] ?? 'bg-faint'
							]}
						></span>
					</span>
				{:else}
					<span class={['size-1.5 flex-none rounded-full', DOT[r.state] ?? 'bg-faint']}></span>
				{/if}
				<span class="grid min-w-0 flex-1 leading-tight">
					<span class="truncate">{r.name}</span>
					<span class="truncate text-[12px] text-muted-foreground">
						{who && r.owner
							? `${r.owner.id === me ? 'You' : r.owner.name.split(' ')[0]} · ${r.project}`
							: r.project}
					</span>
				</span>
				{#if live}
					<span
						class="w-16 flex-none text-right tabular-nums"
						{@attach hint(c ? `${c.metric} ${lastOf(c.curve)}` : '')}
						>{c ? even(lastOf(c.curve)) : ''}</span
					>
					<span
						class="w-14 flex-none text-right text-[12px] text-muted-foreground tabular-nums"
						{@attach hint(r.gpus ?? 'GPUs not known')}>{duration(r.runtime)}</span
					>
				{:else}
					{#if ENDED[r.state]}
						<span class="flex-none text-[12px] text-state-failed">{ENDED[r.state]}</span>
					{/if}
					<span class="w-14 flex-none text-right text-[12px] text-muted-foreground tabular-nums"
						>{relativeTime(r.at, now)}</span
					>
				{/if}
			</a>
		</li>
	{/each}
</ul>
