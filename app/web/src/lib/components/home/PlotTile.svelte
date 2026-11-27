<script lang="ts">
	import XIcon from '@lucide/svelte/icons/x';
	import { fade } from 'svelte/transition';
	import { hint } from '#lib/hint.svelte.ts';
	import CurveFace from '#lib/components/home/CurveFace.svelte';
	import Tile from '#lib/components/home/Tile.svelte';
	import { runCurves, type Curve } from '#lib/home/curves.ts';
	import { cycleMetrics, pins, type Pin } from '#lib/home/pins.svelte.ts';
	import { runHref } from '#lib/workspace/url.ts';

	interface Props {
		pin: Pin;
		index: number;
		draw?: boolean;
	}

	let { pin, index, draw = false }: Props = $props();

	/** Seconds each curve shows before the next, while nobody is looking closely. */
	const DWELL = 6;

	let curves = $state<{ metric: string; curve: Curve }[]>([]);
	let missing = $state(false);
	// The run's curves, read now and on Home's poll while the tab is visible: a live run keeps
	// drawing. The first read picks which curves to step through.
	$effect(() => {
		const { org, project, run, metric } = pin;
		const controller = new AbortController();
		let keys: string[] | undefined;
		const read = () =>
			runCurves(org, project, run, keys, controller.signal)
				.then((series) => {
					keys ??= cycleMetrics(Object.keys(series), metric).filter((k) => series[k]);
					curves = keys.map((k) => ({ metric: k, curve: series[k] })).filter((c) => c.curve);
					missing = !curves.length;
				})
				.catch(() => {
					if (!controller.signal.aborted) missing = true;
				});
		void read();
		const id = setInterval(() => {
			if (document.visibilityState === 'visible') void read();
		}, 30_000);
		return () => {
			clearInterval(id);
			controller.abort();
		};
	});

	// Idle, it steps to the next curve; hovering holds it, and a dot picks one.
	let at = $state(0);
	let held = $state(false);
	const reduced =
		typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
	$effect(() => {
		if (held || curves.length < 2) return;
		const id = setTimeout(() => (at = (at + 1) % curves.length), DWELL * 1000);
		return () => clearTimeout(id);
	});
	const shown = $derived(curves[at % Math.max(1, curves.length)] ?? null);
</script>

<!-- A pinned run (H7): one of its curves at a time, its latest value, how it moved lately, its
     shape. -->
<Tile
	onpointerenter={() => (held = true)}
	onpointerleave={() => (held = false)}
	onfocusin={() => (held = true)}
	onfocusout={() => (held = false)}
	kind="plot"
	title={pin.label}
	href="{runHref(pin.org, pin.project, pin.run)}?tab=charts"
	{index}
>
	{#snippet meta()}
		{#if curves.length > 1}
			<span class="flex items-center gap-1 px-1">
				{#each curves as c, i (c.metric)}
					<button
						type="button"
						class={[
							'size-1.5 rounded-full transition-colors',
							i === at % curves.length
								? 'bg-foreground/70'
								: 'bg-foreground/15 hover:bg-foreground/40'
						]}
						aria-label="Show {c.metric}"
						{@attach hint(c.metric)}
						onclick={() => (at = i)}
					></button>
				{/each}
			</span>
		{/if}
		<button
			type="button"
			class="-mr-1 grid size-5 place-items-center rounded opacity-0 group-hover/tile:opacity-100 hover:bg-muted focus-visible:opacity-100"
			aria-label="Unpin"
			{@attach hint('Unpin')}
			onclick={() => pins.remove(pin)}
		>
			<XIcon class="size-3.5" />
		</button>
	{/snippet}
	{#if shown}
		<!-- Keyed on the metric: the old curve fades out as the next fades and draws in. -->
		<div class="grid min-h-0 flex-1">
			{#key shown.metric}
				<div
					class="col-start-1 row-start-1 flex min-h-0 flex-col"
					in:fade={{ duration: reduced ? 0 : 350, delay: reduced ? 0 : 150 }}
					out:fade={{ duration: reduced ? 0 : 200 }}
				>
					<CurveFace metric={shown.metric} curve={shown.curve} draw={draw || at > 0} />
				</div>
			{/key}
		</div>
	{:else if missing}
		<p class="text-[12px] text-muted-foreground">Not logged</p>
	{/if}
</Tile>
