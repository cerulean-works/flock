<script lang="ts">
	import HouseIcon from '@lucide/svelte/icons/house';
	import MenuIcon from '@lucide/svelte/icons/menu';
	import PanelLeftOpenIcon from '@lucide/svelte/icons/panel-left-open';
	import { afterNavigate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import LogoMark from '#lib/components/LogoMark.svelte';
	import ResizeHandle from '#lib/components/ResizeHandle.svelte';
	import Sidebar from '#lib/components/shell/Sidebar.svelte';
	import WorkspaceSwitcher from '#lib/components/shell/WorkspaceSwitcher.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as Sheet from '#lib/components/ui/sheet/index.js';
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

	// The rail's switcher keeps home and account pages in place, like the sidebar's.
	const stay = $derived(
		!page.route.id?.startsWith('/(app)/[entity]') && !page.route.id?.startsWith('/(app)/orgs/')
	);
</script>

<div class="flex h-full">
	{#if !collapsed}
		<aside
			class="relative hidden flex-none border-r border-divider md:block"
			style:width="{sidebarW}px"
		>
			<Sidebar
				org={data.shell.org}
				orgs={data.shell.orgs}
				projects={data.shell.projects}
				oncollapse={() => setCollapsed(true)}
			/>
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
			<div class="flex h-12 flex-none items-center">
				<Button
					variant="ghost"
					size="icon-sm"
					class="text-muted-foreground"
					aria-label="Show sidebar"
					title="Show sidebar"
					onclick={() => setCollapsed(false)}
				>
					<PanelLeftOpenIcon />
				</Button>
			</div>
			<Button
				variant="ghost"
				size="icon-sm"
				class="mt-3 text-muted-foreground"
				href={resolve('/(app)')}
				aria-label="Home"
				title="Home"
			>
				<HouseIcon />
			</Button>
			<div class="mt-auto py-2">
				<WorkspaceSwitcher org={data.shell.org} orgs={data.shell.orgs} {stay} compact />
			</div>
		</aside>
	{/if}
	<div class="flex min-w-0 flex-1 flex-col">
		<header class="flex h-12 flex-none items-center gap-2 border-b border-divider px-3 md:hidden">
			<Button
				variant="ghost"
				size="icon-sm"
				class="text-muted-foreground"
				aria-label="Show sidebar"
				title="Show sidebar"
				onclick={() => (drawer = true)}
			>
				<MenuIcon />
			</Button>
			<a
				href={resolve('/(app)')}
				class="flex items-center gap-2 rounded-md font-serif text-[19px] tracking-[-0.01em] outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
			>
				<LogoMark class="size-6" />
				<span class="translate-y-[0.13em]">Flock</span>
			</a>
		</header>
		{@render children()}
	</div>
</div>

<Sheet.Root bind:open={drawer}>
	<Sheet.Content side="left" class="w-72 gap-0 p-0" showCloseButton={false}>
		<Sheet.Title class="sr-only">Navigation</Sheet.Title>
		<Sidebar org={data.shell.org} orgs={data.shell.orgs} projects={data.shell.projects} />
	</Sheet.Content>
</Sheet.Root>
