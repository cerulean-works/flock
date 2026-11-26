<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import BoxIcon from '@lucide/svelte/icons/box';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import HouseIcon from '@lucide/svelte/icons/house';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import LogInIcon from '@lucide/svelte/icons/log-in';
	import MenuIcon from '@lucide/svelte/icons/menu';
	import PanelLeftOpenIcon from '@lucide/svelte/icons/panel-left-open';
	import { afterNavigate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
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

	function setCollapsed(value: boolean) {
		collapsed = value;
		document.cookie = `flock_sidebar=${value ? 'collapsed' : 'open'}; path=/; max-age=31536000; samesite=lax`;
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

	// The rail's switcher keeps home and account pages in place, like the sidebar's.
	const stay = $derived(
		!page.route.id?.startsWith('/(app)/[entity]') &&
			!page.route.id?.startsWith('/(app)/orgs/') &&
			!page.route.id?.startsWith('/(app)/datasets/') &&
			!page.route.id?.startsWith('/(app)/models/') &&
			!page.route.id?.startsWith('/(app)/storage/')
	);
</script>

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
				onresize={(px) => (sidebarW = px)}
				oncommit={(px) => saveWidth(SIDEBAR_W, px)}
			/>
		</aside>
	{:else}
		<aside
			class="hidden w-12 flex-none flex-col items-center border-r border-divider bg-sidebar md:flex"
			aria-label="Sidebar"
		>
			<div class="flex h-10 flex-none items-center">
				<Button
					variant="ghost"
					size="icon-sm"
					class="text-muted-foreground hover:bg-sidebar-accent"
					aria-label="Show sidebar"
					{@attach hint('Show sidebar  [')}
					onclick={() => setCollapsed(false)}
				>
					<PanelLeftOpenIcon />
				</Button>
			</div>
			<!-- The workspace first, as in the open sidebar. -->
			<div class="pb-2">
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
			{#if signedIn}
				<Button
					variant="ghost"
					size="icon-sm"
					class={[
						'mt-1 text-muted-foreground',
						page.route.id === '/(app)' && 'bg-sidebar-accent text-foreground'
					]}
					href={resolve('/(app)')}
					aria-label="Home"
					{@attach hint('Home')}
				>
					<HouseIcon />
				</Button>
			{/if}
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
					class={[
						'mt-1 text-muted-foreground',
						page.route.id === '/(app)/[entity]' && 'bg-sidebar-accent text-foreground'
					]}
					href={entityHref(data.shell.org)}
					aria-label="Runs"
					{@attach hint('Runs')}
				>
					<ActivityIcon />
				</Button>
			{/if}
			{#if groupsProject && data.shell.org}
				<Button
					variant="ghost"
					size="icon-sm"
					class={['mt-1 text-muted-foreground', onGroups && 'bg-sidebar-accent text-foreground']}
					href={groupsHref(data.shell.org, groupsProject)}
					aria-label="Groups in {groupsProject}"
					aria-current={onGroups ? 'page' : undefined}
					title="Groups"
				>
					<LayersIcon />
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
					{@attach hint('Datasets')}
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
					{@attach hint('Models')}
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
					{@attach hint('Storage')}
				>
					<HardDriveIcon />
				</Button>
			{/if}
		</aside>
	{/if}
	<div class="flex min-w-0 flex-1 flex-col">
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
			{#if data.shell.org}
				<a
					href={entityHref(data.shell.org)}
					class="flex min-w-0 items-center gap-2 rounded-md text-[13px] font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
				>
					<OrgMark
						name={data.shell.orgName ?? data.shell.org}
						src={data.shell.orgAvatar}
						class="size-5 rounded-[5px] text-[11px]"
					/>
					<span class="truncate">{data.shell.orgName ?? data.shell.org}</span>
				</a>
			{/if}
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
		<Sidebar {...sidebar} />
	</Sheet.Content>
</Sheet.Root>
