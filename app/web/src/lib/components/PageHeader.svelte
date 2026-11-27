<script lang="ts">
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { hint } from '#lib/hint.svelte.ts';
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import OrgMark from '#lib/components/shell/OrgMark.svelte';
	import type { Crumb } from '#lib/storage.ts';
	import { entityHref, projectHref } from '#lib/workspace/url.ts';

	interface Props {
		title: string;
		/**
		 * `workspace` (the default): the fixed-height header of a full-width page. `home`: the
		 * header of a page at Home's width, which flows with the page and may wrap.
		 */
		layout?: 'workspace' | 'home';
		/** Before the title, centred on it: an org's tile, an icon. */
		leading?: Snippet;
		/** Beside the title, centred on it: its ⋯ menu, a state badge. */
		badges?: Snippet;
		/** Muted text on the title's baseline, cut to one line (a description, counts). */
		subtitle?: Snippet;
		/** Hover text for the subtitle, which may be cut short. */
		subtitleTitle?: string;
		/** At the right of the title row. */
		actions?: Snippet;
		/** The row under the title: view tabs, search, filters. Draws the header's bottom border. */
		toolbar?: Snippet;
		/** `home` only: a row above the title, such as breadcrumbs. */
		crumbs?: Snippet;
		/** `home` only: muted reading text under the title, which may wrap. */
		description?: Snippet;
		/**
		 * `workspace` only: the steps between the org (and project) and the title, such as Groups.
		 * The org, and the project on a project's pages, lead the trail by themselves.
		 */
		trail?: Crumb[];
		/**
		 * `workspace` only: lead the trail with the org. Off on Datasets, Models and Storage, whose
		 * trail starts at the tab (the sidebar's foot already names the org).
		 */
		org?: boolean;
		/** `workspace` only: in place of the title's text, the trail's last step (a name to edit). */
		heading?: Snippet;
		/**
		 * `workspace` only: on a phone, the step before the title stays (where this page is).
		 * Off, a phone shows the title alone (a name being edited gets the width).
		 */
		phoneTrail?: boolean;
	}

	let {
		title,
		layout = 'workspace',
		leading,
		badges,
		subtitle,
		subtitleTitle,
		actions,
		toolbar,
		crumbs,
		description,
		trail = [],
		org: withOrg = true,
		heading,
		phoneTrail = true
	}: Props = $props();

	// Where the page is, Linear-style: org / project / … / title, each step a link.
	// `entity` on the workspace pages, `org` on Datasets, Models and Storage.
	const org = $derived(page.params.entity ?? page.params.org ?? null);
	const orgName = $derived(page.data.shell?.orgName ?? org);
	const project = $derived(page.params.project ?? null);
	const steps = $derived<Crumb[]>([
		...(project && title !== project ? [{ label: project, href: projectHref(org!, project) }] : []),
		...trail
	]);
	/** No crumbs before the title (the org's included): a top-level page. */
	const alone = $derived(!steps.length && !(withOrg && org && orgName && title !== orgName));
	const CRUMB =
		'min-w-0 truncate rounded-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50';
</script>

<!--
	A page's header. On the workspace pages (an org's runs, a project, a run) every row has a fixed
	height and nothing wraps, so the title and the toolbar sit at the same place on each of them
	and moving between them never shifts the page. On a `home` page the title row keeps its
	actions at the right edge, so a page's controls never sit half-stretched under the title.
-->
{#if layout === 'home'}
	<header class={['pb-5', crumbs ? 'pt-5' : 'pt-8']}>
		{#if crumbs}
			<div class="pb-3">{@render crumbs()}</div>
		{/if}
		<div class="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-2">
			<div class="flex min-w-0 flex-1 items-baseline gap-x-3">
				<div class="flex min-w-0 items-center gap-3">
					{#if leading}
						<div class="flex flex-none items-center">{@render leading()}</div>
					{/if}
					<h1
						class="min-w-0 truncate text-[34px] leading-[1.15] font-normal tracking-[-0.025em]"
						{title}
					>
						{title}
					</h1>
					{#if badges}
						<div class="flex flex-none items-center gap-2">{@render badges()}</div>
					{/if}
				</div>
				{#if subtitle}
					<div
						class="min-w-0 truncate text-[13px] text-muted-foreground tabular-nums max-sm:hidden"
						{@attach hint(subtitleTitle)}
					>
						{@render subtitle()}
					</div>
				{/if}
			</div>
			{#if actions}
				<div class="ml-auto flex flex-none items-center gap-2">{@render actions()}</div>
			{/if}
		</div>
		{#if description}
			<p class="mt-1 max-w-[62ch] text-muted-foreground">{@render description()}</p>
		{/if}
		{#if toolbar}
			<div class="mt-4 flex min-h-10 flex-wrap items-center gap-2">{@render toolbar()}</div>
		{/if}
	</header>
{:else}
	<header class="flex-none">
		<!-- One 40px row, level with the sidebar's top: the trail, the title as its last step, the
		     page's marks and actions. Nothing wraps, so moving between pages never shifts them. -->
		<div
			class={[
				'flex h-10 items-center gap-x-2 pr-4 pl-5 text-[13px]',
				!toolbar && 'border-b border-divider'
			]}
		>
			<!-- On one baseline: after crumbs the title is their 13px, set apart only by weight and
			     colour. Icons and chevrons centre themselves. -->
			<nav
				aria-label="Breadcrumbs"
				class={['flex min-w-0 items-baseline gap-1.5', !phoneTrail && 'max-sm:flex-1']}
			>
				{#if withOrg && org && orgName && title !== orgName}
					<a
						href={entityHref(org)}
						class={['flex flex-none items-baseline gap-2 max-sm:hidden', CRUMB]}
					>
						<OrgMark
							name={orgName}
							src={page.data.shell?.orgAvatar}
							class="size-4 self-center rounded-[4px] text-[9px]"
						/>
						{orgName}
					</a>
					<ChevronRightIcon
						class="size-3.5 flex-none self-center text-faint max-sm:hidden"
						aria-hidden="true"
					/>
				{/if}
				{#each steps as step, i (`${i}:${step.href}`)}
					<!-- On a phone only the step before the title stays: where this page is. -->
					{@const far = (i < steps.length - 1 || !phoneTrail) && 'max-sm:hidden'}
					<a href={step.href} class={[far, CRUMB]} {@attach hint(step.label)}>{step.label}</a>
					<ChevronRightIcon
						class={['size-3.5 flex-none self-center text-faint', far]}
						aria-hidden="true"
					/>
				{/each}
				{#if leading}
					<!-- Every mark in the trail (the org's, chevrons, a swatch, badges) centres on one
					     line: the trail's box, where the org's mark sits. -->
					<div class="flex h-5 flex-none items-center self-center">
						{@render leading()}
					</div>
				{/if}
				<!-- A title on its own (a top-level page) is the wordmark's 16px, at its own weight;
				     after crumbs it keeps their 13px. -->
				{#if heading}
					{@render heading()}
				{:else}
					<h1
						class={[
							'min-w-0 truncate leading-5 font-medium text-foreground',
							alone ? 'text-[16px] tracking-[-0.015em]' : 'text-[13px]'
						]}
						{@attach hint(subtitleTitle ?? title)}
					>
						{title}
					</h1>
				{/if}
				<!-- In the trail, in a box of the text's height: taller controls (a version picker, a
				     ⋯ button) overflow it rather than stretch the trail, so the trail's marks stay
				     centred on its text, on the org's mark's line. -->
				{#if badges}
					<div class="ml-1 flex h-5 flex-none items-center gap-2 self-center">
						{@render badges()}
					</div>
				{/if}
			</nav>
			{#if subtitle}
				<div class="min-w-0 truncate text-[13px] text-muted-foreground max-sm:hidden">
					{@render subtitle()}
				</div>
			{/if}
			<div class="ml-auto flex flex-none items-center gap-2">
				{#if actions}{@render actions()}{/if}
			</div>
		</div>
		{#if toolbar}
			<!-- Its controls' icons land on the crumbs' 20px edge; underline tabs, which have almost
			     no padding, are nudged so their words do too. -->
			<div
				class="relative flex h-9 items-center gap-1 border-b border-divider pr-4 pl-3 [&>[data-slot=tabs]]:ml-[5px]"
			>
				{@render toolbar()}
			</div>
		{/if}
	</header>
{/if}
