<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Popover from '#lib/components/ui/popover/index.js';
	import { CREDIT_SERVICES, credits as fmt, type Credits } from '#lib/home/credits.ts';
	import { monthName, shiftMonth } from '#lib/utils.ts';
	import { withParam } from '#lib/workspace/url.ts';

	interface Props {
		credits: Credits;
		/** The current month: there's nothing to show after it. */
		thisMonth: string;
		/** Where it shows: in the header (hidden on phones) or above the feed (phones only). */
		class?: string;
	}

	let { credits, thisMonth, class: className }: Props = $props();

	/** The share of the grant still left: the bar reads as the number beside it. */
	const left = $derived(
		credits.granted > 0 ? Math.min(1, Math.max(0, credits.balance) / credits.granted) : 0
	);
	const gone = $derived(left <= 0);
	const near = $derived(!gone && left <= 0.2);
	// Static class strings, so Tailwind sees each one.
	const tone = $derived(gone ? 'bg-state-failed' : near ? 'bg-state-preempting' : 'bg-link');
	const month = $derived(credits.month.month);
	const current = $derived(month >= thisMonth);
	const services = $derived(
		CREDIT_SERVICES.map((s) => ({ ...s, spent: credits.month.by_service[s.key] ?? 0 }))
			.filter((s) => s.spent > 0)
			.sort((a, b) => b.spent - a.spent)
	);
	const maxSpent = $derived(Math.max(...services.map((s) => s.spent), 0));
	let open = $state(false);

	const monthHref = (m: string) => withParam(page.url, 'month', m === thisMonth ? null : m);
</script>

<!-- The NCHC wallet as one line in the header, as the credit dashboard reads it; the month's
     spend by service opens beside it on request. Amber once a fifth of the grant is left. -->
<Popover.Root bind:open>
	<Popover.Trigger>
		{#snippet child({ props })}
			<Button
				{...props}
				variant={open ? 'quiet-on' : 'quiet'}
				size="bar"
				class={[
					'tabular-nums',
					className,
					gone ? 'text-destructive' : near && 'text-state-preempting'
				]}
				{@attach hint('Credits')}
			>
				{fmt(credits.balance)} credits left
				{#if !current}<span class="text-muted-foreground">· {monthName(month)}</span>{/if}
				<ChevronDownIcon class={['transition-transform', open && 'rotate-180']} />
			</Button>
		{/snippet}
	</Popover.Trigger>
	<Popover.Content
		align="end"
		collisionPadding={16}
		class="grid w-[min(20rem,calc(100vw-2rem))] grid-cols-1 gap-5 p-4 text-[13px]"
		aria-labelledby="credits-title"
	>
		<div class="-mt-1 -mr-1 flex h-7 items-center gap-1">
			<h2 id="credits-title" class="min-w-0 flex-1 truncate font-medium text-muted-foreground">
				Credits
			</h2>
			<Button
				variant="quiet"
				size="icon-bar"
				href={monthHref(shiftMonth(month, -1))}
				data-sveltekit-noscroll
				aria-label="Previous month"
				{@attach hint('Previous month')}
			>
				<ChevronLeftIcon />
			</Button>
			<span class="w-26 text-center text-muted-foreground tabular-nums">{monthName(month)}</span>
			<Button
				variant="quiet"
				size="icon-bar"
				class={current ? 'pointer-events-none opacity-40' : undefined}
				href={current ? undefined : monthHref(shiftMonth(month, 1))}
				data-sveltekit-noscroll
				aria-label="Next month"
				{@attach hint('Next month')}
				aria-disabled={current}
			>
				<ChevronRightIcon />
			</Button>
		</div>

		<div class="grid gap-2">
			<p class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
				<span class="text-[24px] leading-none tracking-[-0.02em] tabular-nums">
					{fmt(credits.balance)}
				</span>
			</p>
			<div
				class="h-1 overflow-hidden rounded-full bg-muted"
				role="meter"
				aria-label="Credits left"
				aria-valuemin={0}
				aria-valuemax={100}
				aria-valuenow={Math.round(left * 100)}
			>
				<div class={['h-full rounded-full', tone]} style:width="{left * 100}%"></div>
			</div>
		</div>

		<div>
			<h3 class="flex text-xs font-medium text-muted-foreground">
				<span class="flex-1">Spent in {monthName(month)}</span>
				<span class="tabular-nums">{fmt(credits.month.spent)}</span>
			</h3>
			<ul class="mt-1">
				{#each services as s (s.key)}
					<li class="py-1.5">
						<div class="flex items-baseline gap-2">
							<span class="min-w-0 flex-1 truncate">{s.label}</span>
							<span class="text-muted-foreground tabular-nums">{fmt(s.spent)}</span>
						</div>
						<div class="mt-1 h-0.5 rounded-full bg-muted">
							<div
								class="h-full rounded-full bg-link/60"
								style:width="{maxSpent ? (s.spent / maxSpent) * 100 : 0}%"
							></div>
						</div>
					</li>
				{:else}
					<li class="py-1.5 text-muted-foreground">Nothing spent in {monthName(month)}.</li>
				{/each}
			</ul>
		</div>

		<!-- eslint-disable svelte/no-navigation-without-resolve -- another service -->
		<a
			href={credits.url}
			target="_blank"
			rel="noopener"
			class="-mt-1 flex items-center gap-1 text-link hover:underline"
		>
			Open the credit dashboard <ArrowUpRightIcon class="size-3.5" />
		</a>
		<!-- eslint-enable svelte/no-navigation-without-resolve -->
	</Popover.Content>
</Popover.Root>
