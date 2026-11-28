<script lang="ts" module>
	/** Home's arrival plays once per load of the app (H13), not on every way back to it. */
	let arrived = false;
</script>

<script lang="ts">
	import XIcon from '@lucide/svelte/icons/x';
	import { onMount } from 'svelte';
	import Page from '#lib/components/Page.svelte';
	import BudgetTile from '#lib/components/home/BudgetTile.svelte';
	import CreditsTile from '#lib/components/home/CreditsTile.svelte';
	import ClusterTile from '#lib/components/home/ClusterTile.svelte';
	import PlotTile from '#lib/components/home/PlotTile.svelte';
	import RunRows from '#lib/components/home/RunRows.svelte';
	import Tile from '#lib/components/home/Tile.svelte';
	import CommandMenu from '#lib/components/keys/CommandMenu.svelte';
	import { pollFeed } from '#lib/data/feed.svelte.ts';
	import { pins } from '#lib/home/pins.svelte.ts';
	import { greeting } from '#lib/home/greeting.ts';
	import { homeStatus } from '#lib/home/status.ts';
	import { hint } from '#lib/hint.svelte.ts';
	import { keyboard } from '#lib/keys.svelte.ts';
	import { liveFirst, type HomeRun } from '#lib/home/runs.ts';
	import { LIVE_STATES } from '#lib/types.ts';
	import LiveChart from '#lib/components/home/LiveChart.svelte';
	import { runHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// The first time in, the field rises and the tiles follow; after that Home just shows.
	const arrive = !arrived;
	// eslint-disable-next-line no-useless-assignment -- module state, read by the next Home
	arrived = true;

	$effect(() => pollFeed());

	let menu = $state<ReturnType<typeof CommandMenu> | null>(null);
	let showing = $state(false);
	// The cursor starts in the field, and Cmd+K comes back to it rather than opening the dialog.
	onMount(() => {
		menu?.focus();
		keyboard.search = () => menu?.focus(true);
		return () => {
			keyboard.search = null;
		};
	});

	const now = $derived(data.org ? data.now : 0);

	/** Runs the tile lists; it scrolls past what fits its 2x2 cells. */
	const ROWS = 30;
	// One list, the viewer's first (Harry: "merged, but bias my runs first"): their live runs, the
	// team's live runs (so it's less like fighting alone), then the viewer's that just ended.
	const isLive = (r: HomeRun) => LIVE_STATES.has(r.state);
	const mineAll = $derived(data.org ? liveFirst(data.mine) : []);
	const teamAll = $derived(data.org ? liveFirst(data.team) : []);
	const runs = $derived(
		[
			...mineAll.filter(isLive),
			...teamAll.filter(isLive),
			...mineAll.filter((r) => !isLive(r)),
			...teamAll.filter((r) => !isLive(r))
		].slice(0, ROWS)
	);
	const liveAll = $derived([...mineAll.filter(isLive), ...teamAll.filter(isLive)].slice(0, 8));
	const me = $derived(data.shell.me?.id ?? null);
	// The Runs list and the live runs, in step: a hovered row's run comes into the pair; the
	// pair's rows are lit.
	let rowHovered = $state<string | null>(null);
	let liveShown = $state<string[]>([]);

	// The welcome (Harry: "invite people back"): one line from the viewer's clock, their live
	// runs and when they were last here. Written once mounted, as the server's clock isn't theirs.
	let welcome = $state<string | null>(null);
	onMount(() => {
		const now = new Date();
		const today = now.toDateString();
		const store = (key: string, value?: string) => {
			try {
				if (value === undefined) return localStorage.getItem(key);
				localStorage.setItem(key, value);
			} catch {
				// Storage off: every visit reads as the first, which only costs the gap lines.
			}
			return null;
		};
		const last = Number(store('flock:home:last-visit')) || null;
		const g = greeting({
			now,
			name: data.shell.me?.name?.split(' ')[0] ?? null,
			live: mineAll.filter(isLive).map((r) => ({ name: r.name, runtime: r.runtime })),
			lastVisit: last,
			eggToday: store('flock:home:egg-day') === today,
			roll: Math.random()
		});
		welcome = g.text;
		store('flock:home:last-visit', String(now.getTime()));
		if (g.egg) store('flock:home:egg-day', today);
	});
	// The field (and Cmd+K) finds the runs shown here by name.
	$effect(() => {
		keyboard.runs = [...mineAll, ...teamAll].map((r) => ({
			label: r.name,
			href: runHref(r.org, r.project, r.id),
			project: r.project
		}));
		return () => {
			keyboard.runs = [];
		};
	});

	// Pins live in this browser: the server can't draw them, so they join once mounted.
	let mounted = $state(false);
	onMount(() => (mounted = true));
	const pinned = $derived(data.org && mounted ? pins.of(data.org) : []);

	// ---- The status tab (H3): since the viewer last looked, kept in this browser -------------
	const SEEN = 'flock:home-seen';
	const DISMISSED = 'flock:home-dismissed';
	let since = $state<number | null>(null);
	let dismissed = $state<Set<string>>(new Set());
	onMount(() => {
		try {
			since = Number(localStorage.getItem(SEEN)) || now - 86_400;
			dismissed = new Set(JSON.parse(localStorage.getItem(DISMISSED) ?? '[]'));
		} catch {
			since = now - 86_400;
		}
		const seen = () => {
			try {
				localStorage.setItem(SEEN, String(Date.now() / 1000));
			} catch {
				// Private windows: every visit is the first.
			}
		};
		addEventListener('pagehide', seen);
		return () => {
			removeEventListener('pagehide', seen);
			seen();
		};
	});
	const status = $derived(
		data.org && since != null ? homeStatus(data.mine, data.budget, since, now, dismissed) : null
	);
	function dismiss(id: string) {
		dismissed = new Set([...dismissed, id]);
		try {
			localStorage.setItem(DISMISSED, JSON.stringify([...dismissed].slice(-50)));
		} catch {
			// Kept for the visit.
		}
	}
	const TONE = {
		failed: 'bg-state-failed',
		warn: 'bg-state-preempting',
		done: 'bg-state-finished'
	} as const;

	// Tiles in the order they flow: runs (2x2), budget and cluster (1x1), plots (2x1).
	let order = 0;
	const next = () => order++;
</script>

<svelte:head>
	<title>Flock</title>
</svelte:head>

{#snippet above()}
	{#if status}
		{@const s = status}
		<!-- A band the field's width, tucked 16px behind its top edge so the field's corners sit
		     over it (Harry: not a tab off to one side): one fact, one action. -->
		<div
			class="status absolute inset-x-0 bottom-[calc(100%-16px)] z-0 flex min-w-0 items-center gap-2 rounded-t-2xl bg-muted pt-2 pr-4 pb-6 pl-5 text-[12px] text-muted-foreground"
		>
			<span class="size-1.5 flex-none rounded-full {TONE[s.tone]}"></span>
			<span class="min-w-0 truncate text-foreground/80">{s.text}</span>
			<span aria-hidden="true">·</span>
			<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- built with resolve() -->
			<a href={s.action.href} class="flex-none font-medium text-foreground hover:underline"
				>{s.action.label}</a
			>
			<button
				type="button"
				class="ml-auto grid size-5 flex-none place-items-center rounded hover:bg-background/60"
				aria-label="Dismiss"
				{@attach hint('Dismiss')}
				onclick={() => dismiss(s.id)}
			>
				<XIcon class="size-3" />
			</button>
		</div>
	{/if}
{/snippet}

{#if !data.org}
	<Page width="home">
		<div class="mx-auto mt-[12vh] max-w-160 text-[17px] leading-[1.45]">
			<h1 class="mb-4 text-[40px] leading-tight tracking-[-0.02em]">No organization yet</h1>
			<p>Ask an admin to invite you.</p>
		</div>
	</Page>
{:else}
	<Page width="full">
		<main class="min-h-0 flex-1 overflow-y-auto px-4 md:px-6" class:arrive>
			<!-- One block (Harry): the welcome, the field and the tiles, centred in the window a touch
			     above the middle; taller than the window, it starts at the top and scrolls. -->
			<div class="flex min-h-full flex-col justify-center pt-10 pb-[max(2.5rem,6vh)]">
				<div class="rise relative z-20 mx-auto w-full max-w-240" style:--i="0">
					<!-- While the status band shows, the greeting rises to make room; the field stays put. -->
					<div
						class={[
							'mb-6 min-h-9 text-center transition-transform duration-300 ease-out',
							status && '-translate-y-6'
						]}
					>
						{#if welcome}
							<h1 class="greet text-[28px] leading-tight font-medium tracking-[-0.02em]">
								{welcome}
							</h1>
						{/if}
					</div>
					<CommandMenu
						bind:this={menu}
						variant="inline"
						open
						onshowing={(v) => (showing = v)}
						{above}
					/>
				</div>
				<div
					class={[
						'mx-auto mt-10 grid w-full max-w-240 grid-flow-dense grid-cols-2 gap-3 transition-opacity duration-200 md:auto-rows-[10.5rem] md:grid-cols-4',
						showing && 'pointer-events-none opacity-25'
					]}
					aria-hidden={showing}
				>
					{#if runs.length}
						<Tile kind="runs" title="Runs" bare scroll index={next() + 1}>
							<RunRows
								{runs}
								{now}
								{me}
								who
								lit={liveShown}
								onhover={(key) => (rowHovered = key)}
							/>
						</Tile>
					{/if}
					<!-- Right of the runs (2x2): Budget and Cluster a cell each, then the first pin (or the
				     live runs) as a 2x1 strip; more pins follow as strips. -->
					{#if data.credits || data.creditsDown}
						<CreditsTile credits={data.credits} down={data.creditsDown} index={next() + 1} />
					{:else if data.budget}
						<BudgetTile budget={data.budget} index={next() + 1} />
					{/if}
					<!-- Always its cell: faded when no Slurm dashboard is connected, so the grid keeps its shape. -->
					<ClusterTile cluster={data.cluster} down={data.clusterDown} index={next() + 1} />
					{#each pinned.slice(0, 1) as pin (`${pin.project}/${pin.run}/${pin.metric}`)}
						<PlotTile {pin} index={next() + 1} draw={arrive} />
					{:else}
						{#if liveAll.length}
							<LiveChart
								runs={liveAll}
								{me}
								hovered={rowHovered}
								onshow={(keys) => (liveShown = keys)}
								index={next() + 1}
							/>
						{/if}
					{/each}
					{#each pinned.slice(1) as pin (`${pin.project}/${pin.run}/${pin.metric}`)}
						<PlotTile {pin} index={next() + 1} draw={arrive} />
					{/each}
				</div>
			</div>
		</main>
	</Page>
{/if}

<style>
	/* The arrival (H13): the field rises into place, the tiles follow a beat apart. Movement is
	   small and quick; with reduced motion everything is simply there. */
	.arrive :global(.rise),
	.arrive :global(.tile) {
		animation: rise 420ms cubic-bezier(0.2, 0.8, 0.25, 1) calc(var(--i, 0) * 45ms + 60ms) both;
	}
	.greet {
		animation: rise 500ms cubic-bezier(0.2, 0.8, 0.25, 1) var(--d, 0ms) both;
	}
	.status {
		animation: tab-up 260ms cubic-bezier(0.2, 0.8, 0.25, 1) 350ms both;
	}
	@keyframes rise {
		from {
			opacity: 0;
			transform: translateY(8px);
		}
	}
	@keyframes tab-up {
		from {
			opacity: 0;
			transform: translateY(100%);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.arrive :global(.rise),
		.arrive :global(.tile),
		.greet {
			animation: rise 500ms cubic-bezier(0.2, 0.8, 0.25, 1) var(--d, 0ms) both;
		}
		.status {
			animation: none;
		}
	}
</style>
