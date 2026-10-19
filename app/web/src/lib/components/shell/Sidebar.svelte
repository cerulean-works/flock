<script lang="ts">
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import HouseIcon from '@lucide/svelte/icons/house';
	import PanelLeftCloseIcon from '@lucide/svelte/icons/panel-left-close';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SearchIcon from '@lucide/svelte/icons/search';
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import { cubicOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { fly } from 'svelte/transition';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import LogoMark from '#lib/components/LogoMark.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { ProjectInfo, Role } from '#lib/types.ts';
	import { entityHref, projectHref } from '#lib/workspace/url.ts';
	import OrgMark from './OrgMark.svelte';
	import WorkspaceSwitcher from './WorkspaceSwitcher.svelte';

	interface Props {
		org: string | null;
		orgs: { name: string; role: Role; avatar_url: string | null }[];
		projects: ProjectInfo[];
		/** Hide the sidebar (wide windows only: the drawer closes by itself). */
		oncollapse?: () => void;
	}

	let { org, orgs, projects, oncollapse }: Props = $props();

	/** Inside an org (its runs, a project, its members): back home, and its projects. */
	const inOrg = $derived(
		!!page.route.id?.startsWith('/(app)/[entity]') || !!page.route.id?.startsWith('/(app)/orgs/')
	);
	const route = $derived(page.route.id);

	/** Slide a whole menu's width; `dir` 1 is from (or to) the right. */
	const swipe = (dir: 1 | -1) => ({
		x: `${dir * 100}%`,
		opacity: 1,
		duration: prefersReducedMotion.current ? 0 : 260,
		easing: cubicOut
	});

	const SHOWN = 12;
	let showAll = $state(false);
	let searching = $state(false);
	let query = $state('');
	const matches = $derived(
		query.trim()
			? projects.filter((p) => p.name.toLowerCase().includes(query.trim().toLowerCase()))
			: projects
	);
	const shown = $derived(showAll || query.trim() ? matches : matches.slice(0, SHOWN));

	let creating = $state(false);

	const ITEM =
		'flex h-8 items-center gap-2.5 rounded-lg px-2 text-[13.5px] text-foreground outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-muted aria-[current=page]:font-medium [&_svg]:size-4 [&_svg]:flex-none [&_svg]:text-muted-foreground';
</script>

<div class="flex h-full flex-col bg-sidebar text-sidebar-foreground">
	<div class="flex h-12 flex-none items-center gap-2 border-b border-divider pr-2 pl-4">
		<a
			href={resolve('/(app)')}
			class="flex items-center gap-2 rounded-md font-serif text-[19px] tracking-[-0.01em] outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
		>
			<LogoMark class="size-6" />
			<!-- Newsreader's line box leaves room for descenders "Flock" doesn't have: centered as
			     is, the letters sit high of the disc. This moves their ink to its middle. -->
			<span class="translate-y-[0.13em]">Flock</span>
		</a>
		{#if oncollapse}
			<Button
				variant="ghost"
				size="icon-sm"
				class="ml-auto text-muted-foreground"
				aria-label="Hide sidebar"
				title="Hide sidebar"
				onclick={oncollapse}
			>
				<PanelLeftCloseIcon />
			</Button>
		{/if}
	</div>

	<!-- Going into an org (its runs, members or a project) swipes its menu in from the right, and
	     going home swipes back. Both menus share one grid cell while they pass. -->
	<div class="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)] overflow-hidden">
		{#key inOrg}
			<nav
				class="col-start-1 row-start-1 min-h-0 overflow-y-auto px-2 py-3"
				aria-label="Main"
				in:fly={swipe(inOrg ? 1 : -1)}
				out:fly={swipe(inOrg ? -1 : 1)}
			>
				{#if inOrg}
					<a class={ITEM} href={resolve('/(app)')}><ArrowLeftIcon /> Home</a>
					{#if org}
						<a
							class={[ITEM, 'mt-3 h-10']}
							href={entityHref(org)}
							aria-current={route === '/(app)/[entity]' ? 'page' : undefined}
							title="Latest runs in {org}"
						>
							<OrgMark
								name={org}
								src={orgs.find((o) => o.name === org)?.avatar_url}
								class="size-6 text-[13px]"
							/>
							<span class="truncate font-serif text-[17px]">{org}</span>
						</a>
					{/if}
				{:else}
					<a
						class={ITEM}
						href={resolve('/(app)')}
						aria-current={route === '/(app)' ? 'page' : undefined}
					>
						<HouseIcon /> Home
					</a>
					{#if org}
						<a class={ITEM} href={entityHref(org)}><ActivityIcon /> Runs</a>
					{/if}
				{/if}

				{#if org}
					<div class="mx-2 mt-4 mb-1 flex h-7 items-center gap-1 border-t border-divider pt-4">
						<h2 class="flex-1 text-xs font-medium text-muted-foreground">Projects</h2>
						<Button
							variant="ghost"
							size="icon-xs"
							class={['text-muted-foreground', searching && 'bg-muted']}
							aria-label="Find a project"
							title="Find a project"
							aria-pressed={searching}
							onclick={() => {
								searching = !searching;
								query = '';
							}}
						>
							<SearchIcon />
						</Button>
						<Button
							variant="ghost"
							size="icon-xs"
							class="text-muted-foreground"
							aria-label="New project"
							title="New project"
							onclick={() => (creating = true)}
						>
							<PlusIcon />
						</Button>
					</div>
					{#if searching}
						<!-- svelte-ignore a11y_autofocus -->
						<input
							class="mx-2 mb-1 h-7 w-[calc(100%-1rem)] rounded-md border border-input bg-background px-2 text-[13px] outline-none placeholder:text-faint focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
							placeholder="Find a project…"
							aria-label="Find a project"
							bind:value={query}
							autofocus
							onkeydown={(e) => {
								if (e.key === 'Escape') {
									searching = false;
									query = '';
								}
							}}
						/>
					{/if}
					<ul>
						{#each shown as p (p.name)}
							<li>
								<a
									class={ITEM}
									href={projectHref(org, p.name)}
									aria-current={page.params.project === p.name ? 'page' : undefined}
									title={p.description ?? p.name}
								>
									<FolderIcon />
									<span class="min-w-0 flex-1 truncate">{p.name}</span>
									{#if p.active_run_count}
										<span
											class="size-1.5 flex-none animate-live rounded-full bg-state-running text-state-running motion-reduce:animate-none"
											title="{p.active_run_count} running"
										></span>
									{/if}
								</a>
							</li>
						{:else}
							<li class="px-2 py-1 text-[13px] text-muted-foreground">
								{query.trim() ? 'No matching projects' : 'No projects yet'}
							</li>
						{/each}
					</ul>
					{#if matches.length > shown.length}
						<button
							class="mt-1 px-2 text-xs text-muted-foreground hover:text-foreground"
							onclick={() => (showAll = true)}
						>
							Show {matches.length - shown.length} more
						</button>
					{/if}
				{/if}
			</nav>
		{/key}
	</div>

	<div class="flex-none border-t border-divider p-2">
		<WorkspaceSwitcher {org} {orgs} stay={!inOrg} />
	</div>
</div>

{#if org}<ProjectDialog bind:open={creating} entity={org} />{/if}
