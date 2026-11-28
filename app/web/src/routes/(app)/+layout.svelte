<script lang="ts">
	import { tick } from 'svelte';
	import { hint } from '#lib/hint.svelte.ts';
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import BoxIcon from '@lucide/svelte/icons/box';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import LogInIcon from '@lucide/svelte/icons/log-in';
	import MenuIcon from '@lucide/svelte/icons/menu';
	import SidebarToggleIcon from '#lib/components/shell/SidebarToggleIcon.svelte';
	import { afterNavigate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import LogoMark from '#lib/components/LogoMark.svelte';
	import KeyboardLayer from '#lib/components/keys/KeyboardLayer.svelte';
	import ResizeHandle from '#lib/components/ResizeHandle.svelte';
	import OrgMark from '#lib/components/shell/OrgMark.svelte';
	import Sidebar from '#lib/components/shell/Sidebar.svelte';
	import WorkspaceSwitcher from '#lib/components/shell/WorkspaceSwitcher.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Sheet from '#lib/components/ui/sheet/index.js';
	import { datasetsHref } from '#lib/datasets.ts';
	import { groupsHref } from '#lib/groups.ts';
	import { modelsHref } from '#lib/models.ts';
	import { entityHref, loginHref } from '#lib/workspace/url.ts';
	import { storageHref } from '#lib/storage.ts';
	import { SIDEBAR_W, saveWidth } from '#lib/widths.ts';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	// Wide windows show the sidebar beside the page, or a rail once it's hidden; narrow ones a
	// top bar that opens it as a drawer. Pages have no header of their own.
	let collapsed = $derived(data.shell.collapsed);
	let sidebarW = $derived(data.shell.sidebarW);
	let drawer = $state(false);

	async function setCollapsed(value: boolean) {
		collapsed = value;
		document.cookie = `flock_sidebar=${value ? 'collapsed' : 'open'}; path=/; max-age=31536000; samesite=lax`;
		// Focus left with the control that was removed: onto the one that undoes it.
		await tick();
		if (!document.activeElement || document.activeElement === document.body)
			document
				.querySelector<HTMLElement>(`[aria-label="${value ? 'Show sidebar' : 'Hide sidebar'}"]`)
				?.focus();
	}

	afterNavigate(() => (drawer = false));

	const signedIn = $derived(!!data.shell.me);
	// From outside the org: none of its members' pages, and the org itself in the sidebar.
	const outside = $derived(data.shell.access === 'public');
	const sidebar = $derived({
		org: data.shell.org,
		orgs: data.shell.orgs,
		projects: data.shell.projects,
		s3: data.shell.s3,
		access: data.shell.access,
		orgName: data.shell.orgName,
		orgAvatar: data.shell.orgAvatar,
		signedIn
	});

	/** On a project's pages with groups (D215), the rail links to them, as the sidebar does. */
	const groupsProject = $derived(
		data.shell.org &&
			page.params.project &&
			page.route.id?.startsWith('/(app)/[entity]/[project]') &&
			page.data.groups
			? page.params.project
			: null
	);
	const onGroups = $derived(!!page.route.id?.startsWith('/(app)/[entity]/[project]/groups'));
	/** Runs: an org's, or a project's runs (anything under a project but its groups). */
	const onRuns = $derived(
		!!page.route.id?.startsWith('/(app)/[entity]') &&
			!onGroups &&
			!page.route.id?.startsWith('/(app)/[entity]/[project]/datasets') &&
			!page.route.id?.startsWith('/(app)/[entity]/[project]/models')
	);

	// The rail's switcher keeps home and account pages in place, like the sidebar's.
	const stay = $derived(
		!page.route.id?.startsWith('/(app)/[entity]') &&
			!page.route.id?.startsWith('/(app)/orgs/') &&
			!page.route.id?.startsWith('/(app)/datasets/') &&
			!page.route.id?.startsWith('/(app)/models/') &&
			!page.route.id?.startsWith('/(app)/storage/')
	);
</script>

<!-- Past the sidebar to the page, for the keyboard: shown on the first Tab. -->
<a
	href="#content"
	class="fixed top-2 left-2 z-50 -translate-y-16 rounded-md bg-background px-3 py-1.5 text-[13px] font-medium shadow-md outline-none focus-visible:translate-y-0 focus-visible:ring-3 focus-visible:ring-ring/50"
	>Skip to content</a
>
<div class="flex h-full">
	{#if !collapsed}
		<aside
			class="relative hidden flex-none border-r border-divider bg-sidebar md:block"
			style:width="{sidebarW}px"
		>
			<Sidebar {...sidebar} oncollapse={() => setCollapsed(true)} />
			<ResizeHandle
				value={sidebarW}
				bounds={SIDEBAR_W}
				label="Resize sidebar"
				oncollapse={() => setCollapsed(true)}
				onresize={(px) => (sidebarW = px)}
				oncommit={(px) => saveWidth(SIDEBAR_W, px)}
			/>
		</aside>
	{:else}
		<aside
			class="relative hidden w-12 flex-none flex-col items-center border-r border-divider bg-sidebar md:flex"
			aria-label="Sidebar"
		>
			<!-- The edge opens it again, as a click on the open sidebar's edge closes it: the same
			     7px target and hover line. The Show sidebar button is its keyboard way. -->
			<button
				type="button"
				tabindex="-1"
				aria-hidden="true"
				class="group/edge absolute inset-y-0 -right-[4px] z-10 w-[7px] cursor-e-resize outline-none"
				aria-label="Show sidebar"
				{@attach hint('Click to open  [')}
				onclick={() => setCollapsed(false)}
			>
				<span
					class="absolute inset-y-0 left-[3px] w-px bg-transparent transition-colors group-hover/edge:bg-border"
				></span>
			</button>
			<div class="flex h-10 flex-none items-center">
				<Button
					variant="ghost"
					size="icon-sm"
					class="text-muted-foreground hover:bg-sidebar-accent"
					aria-label="Show sidebar"
					{@attach hint('Show sidebar  [')}
					onclick={() => setCollapsed(false)}
				>
					<SidebarToggleIcon open={false} />
				</Button>
			</div>
			{#if outside && data.shell.org}
				<a
					class="mt-3 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
					href={entityHref(data.shell.org)}
					aria-label={data.shell.orgName ?? data.shell.org}
					{@attach hint(data.shell.orgName ?? data.shell.org)}
				>
					<OrgMark name={data.shell.orgName ?? data.shell.org} src={data.shell.orgAvatar} />
				</a>
			{:else if data.shell.org}
				<Button
					variant="ghost"
					size="icon-sm"
					class={['mt-1 text-muted-foreground', onRuns && 'bg-sidebar-accent text-foreground']}
					href={entityHref(data.shell.org)}
					aria-current={onRuns ? 'page' : undefined}
					aria-label="Runs"
					{@attach hint('Runs  G then R')}
				>
					<ActivityIcon />
				</Button>
			{/if}
			{#if data.shell.s3 && data.shell.org && !outside}
				<Button
					variant="ghost"
					size="icon-sm"
					class={[
						'mt-1 text-muted-foreground',
						page.route.id?.startsWith('/(app)/datasets/') && 'bg-sidebar-accent text-foreground'
					]}
					href={datasetsHref(data.shell.org)}
					aria-label="Datasets"
					{@attach hint('Datasets  G then D')}
				>
					<DatabaseIcon />
				</Button>
				<Button
					variant="ghost"
					size="icon-sm"
					class={[
						'mt-1 text-muted-foreground',
						page.route.id?.startsWith('/(app)/models/') && 'bg-sidebar-accent text-foreground'
					]}
					href={modelsHref(data.shell.org)}
					aria-label="Models"
					{@attach hint('Models  G then M')}
				>
					<BoxIcon />
				</Button>
				<Button
					variant="ghost"
					size="icon-sm"
					class={[
						'mt-1 text-muted-foreground',
						page.route.id?.startsWith('/(app)/storage/') && 'bg-sidebar-accent text-foreground'
					]}
					href={storageHref(data.shell.org)}
					aria-label="Storage"
					{@attach hint('Storage  G then S')}
				>
					<HardDriveIcon />
				</Button>
			{/if}
			{#if groupsProject && data.shell.org}
				<!-- The project's own page, apart from the org's. -->
				<span class="my-2 h-px w-5 bg-divider" aria-hidden="true"></span>
				<Button
					variant="ghost"
					size="icon-sm"
					class={['text-muted-foreground', onGroups && 'bg-sidebar-accent text-foreground']}
					href={groupsHref(data.shell.org, groupsProject)}
					aria-label="Groups in {groupsProject}"
					aria-current={onGroups ? 'page' : undefined}
					title="Groups"
				>
					<LayersIcon />
				</Button>
			{/if}
			<div class="mt-auto py-2">
				{#if signedIn}
					<WorkspaceSwitcher
						org={outside ? null : data.shell.org}
						orgs={data.shell.orgs}
						{stay}
						compact
					/>
				{:else}
					<Button
						variant="ghost"
						size="icon-sm"
						class="text-muted-foreground"
						href={loginHref(page.url)}
						data-sveltekit-reload
						aria-label="Sign in"
						{@attach hint('Sign in')}
					>
						<LogInIcon />
					</Button>
				{/if}
			</div>
		</aside>
	{/if}
	<div id="content" class="flex min-w-0 flex-1 flex-col outline-none" tabindex="-1">
		<header
			class="flex h-11 flex-none items-center gap-2 border-b border-divider bg-sidebar px-3 md:hidden"
		>
			<Button
				variant="ghost"
				size="icon-sm"
				class="text-muted-foreground"
				aria-label="Show sidebar"
				{@attach hint('Show sidebar')}
				onclick={() => (drawer = true)}
			>
				<MenuIcon />
			</Button>
			<a
				href={resolve('/(app)')}
				class="flex items-center gap-2 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
			>
				<LogoMark class="size-4" />
				<span class="text-[16px] font-semibold tracking-[-0.015em]">Flock</span>
			</a>
		</header>
		{@render children()}
	</div>
</div>

{#if signedIn}
	<KeyboardLayer ontogglesidebar={() => setCollapsed(!collapsed)} />
{/if}

<Sheet.Root bind:open={drawer}>
	<Sheet.Content side="left" class="w-72 gap-0 p-0" showCloseButton={false}>
		<Sheet.Title class="sr-only">Navigation</Sheet.Title>
		<Sidebar {...sidebar} drawer oncollapse={() => (drawer = false)} />
	</Sheet.Content>
</Sheet.Root>
