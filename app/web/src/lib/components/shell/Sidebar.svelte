<script lang="ts">
	import BoxIcon from '@lucide/svelte/icons/box';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import HouseIcon from '@lucide/svelte/icons/house';
	import PanelLeftCloseIcon from '@lucide/svelte/icons/panel-left-close';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SearchIcon from '@lucide/svelte/icons/search';
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import LogInIcon from '@lucide/svelte/icons/log-in';
	import { untrack } from 'svelte';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import XIcon from '@lucide/svelte/icons/x';
	import { toast } from 'svelte-sonner';
	import { invalidate } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import LogoMark from '#lib/components/LogoMark.svelte';
	import ProjectDialog from '#lib/components/ProjectDialog.svelte';
	import ProjectMenu from '#lib/components/ProjectMenu.svelte';
	import GroupMenu from '#lib/components/groups/GroupMenu.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { datasetsHref } from '#lib/datasets.ts';
	import {
		GROUPS,
		GroupsError,
		fromFlockFilter,
		groupHref,
		groupsApi,
		groupsCall,
		groupsHref,
		newGroupHref,
		ungroupedHref,
		type GroupList,
		type Suggestion
	} from '#lib/groups.ts';
	import { modelsHref } from '#lib/models.ts';
	import { storageHref } from '#lib/storage.ts';
	import type { Access, OrgEntry, ProjectInfo } from '#lib/types.ts';
	import { cn } from '#lib/utils.ts';
	import { entityHref, loginHref, projectHref } from '#lib/workspace/url.ts';
	import OrgMark from './OrgMark.svelte';
	import WorkspaceSwitcher from './WorkspaceSwitcher.svelte';

	interface Props {
		/** The organization in view, by ID. */
		org: string | null;
		orgs: OrgEntry[];
		projects: ProjectInfo[];
		/** Whether the server has S3 storage: the Datasets, Models and Storage items show only then. */
		s3?: boolean;
		/**
		 * What the viewer may do in the org. From outside it (`public`), the sidebar shows the org
		 * itself at the top, its public projects, and none of its members' pages (D116).
		 */
		access?: Access;
		/** The org as outsiders see it: what it's called, and its picture. */
		orgName?: string | null;
		orgAvatar?: string | null;
		/** Someone is signed in: without that, a Sign in button stands for the account menu. */
		signedIn?: boolean;
		/** Hide the sidebar (wide windows only: the drawer closes by itself). */
		oncollapse?: () => void;
	}

	let {
		org,
		orgs,
		projects,
		s3 = false,
		access = 'member',
		orgName = null,
		orgAvatar = null,
		signedIn = true,
		oncollapse
	}: Props = $props();

	const outside = $derived(access === 'public');

	/** Inside an org (its runs, a project, its members, its datasets, models and storage), where switching
	 * orgs leaves the page. */
	const inOrg = $derived(
		!!page.route.id?.startsWith('/(app)/[entity]') ||
			!!page.route.id?.startsWith('/(app)/orgs/') ||
			!!page.route.id?.startsWith('/(app)/datasets/') ||
			!!page.route.id?.startsWith('/(app)/models/') ||
			!!page.route.id?.startsWith('/(app)/storage/')
	);
	const route = $derived(page.route.id);

	/**
	 * The project in view (D169). On its pages the sidebar slides over to the project's own:
	 * the way back, then its runs, datasets and models.
	 */
	const project = $derived(
		org && page.params.project && route?.startsWith('/(app)/[entity]/[project]')
			? page.params.project
			: null
	);
	// The project panel keeps the last project while it slides back out. It reads `project` first,
	// since effects don't run in server rendering: the page arrives with the panel filled.
	let lastProject = $state<string | null>(null);
	$effect.pre(() => {
		if (project) lastProject = project;
	});
	const shownProject = $derived(project ?? lastProject);
	const projectRoute = '/(app)/[entity]/[project]';

	// The project's groups and suggestions (D215), from its layout's load; kept, like the
	// project, while the panel slides back out.
	// Suggestions arrive as a promise the layout doesn't wait for: they're shown once it resolves,
	// and a different project's are cleared at once rather than shown under this one.
	let streamed = $state<{ project: string | null; list: Suggestion[] }>({
		project: null,
		list: []
	});
	$effect(() => {
		const pending = page.data.suggestions as Promise<Suggestion[]> | Suggestion[] | undefined;
		const name = project;
		if (!name) return;
		// Read without tracking: this effect writes `streamed`, and must re-run only for new data.
		if (untrack(() => streamed.project) !== name) streamed = { project: name, list: [] };
		let current = true;
		void Promise.resolve(pending ?? []).then((list) => {
			if (current) streamed = { project: name, list };
		});
		return () => {
			current = false;
		};
	});
	let lastGroups = $state<{ list: GroupList | null; suggestions: Suggestion[] }>({
		list: null,
		suggestions: []
	});
	$effect.pre(() => {
		if (project)
			lastGroups = {
				list: (page.data.groups as GroupList | null | undefined) ?? null,
				suggestions: streamed.list
			};
	});
	const groupList = $derived(
		project ? ((page.data.groups as GroupList | null | undefined) ?? null) : lastGroups.list
	);
	const suggestions = $derived(project ? streamed.list : lastGroups.suggestions);
	const groupRoute = `${projectRoute}/groups`;
	const groupId = $derived(Number(page.params.group));

	/** Dismissing a suggestion remembers it (D219): it isn't offered again unless its runs change. */
	async function dismiss(org: string, s: Suggestion) {
		if (!shownProject) return;
		try {
			await groupsCall(groupsApi(org, shownProject, 'suggestions/dismiss'), {
				method: 'POST',
				body: { signature: s.signature }
			});
			await invalidate(GROUPS);
		} catch (err) {
			toast.error(err instanceof GroupsError ? err.message : 'Couldn’t dismiss the suggestion');
		}
	}

	/** A suggestion opens the create page with its runs, and a W&B group's filter (D219). */
	const suggestionHref = (org: string, s: Suggestion) =>
		newGroupHref(org, shownProject!, {
			runs: s.filter ? [] : s.runs,
			filters: (s.filter && fromFlockFilter(s.filter)) || [],
			name: s.name
		});

	// The group rows: a 20px slot for the color square, so names line up with each other.
	const SUB =
		'flex h-7 items-center gap-2 rounded-md px-1.5 text-[13px] text-foreground outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-muted aria-[current=page]:font-medium';

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

	// Every row leads with a 24px slot (an icon centred in it), so labels start at one place.
	const ITEM =
		'flex h-8 items-center gap-2.5 rounded-lg px-1 text-[13.5px] text-foreground outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-muted aria-[current=page]:font-medium [&_svg]:size-4 [&_svg]:flex-none [&_svg]:text-muted-foreground [&>svg]:mx-1';
</script>

<div class="flex h-full flex-col bg-sidebar text-sidebar-foreground">
	<div class="flex h-12 flex-none items-center gap-2 border-b border-divider pr-2 pl-4">
		<a
			href={resolve('/(app)')}
			class="flex items-center gap-2 rounded-md text-[19px] font-medium tracking-[-0.025em] outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
		>
			<LogoMark class="size-6" />
			<span>Flock</span>
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

	<nav class="min-h-0 flex-1 overflow-x-hidden overflow-y-auto" aria-label="Main">
		<!-- Two panels side by side: the org's, and on a project's pages the project's, slid in
		     from the right. The one out of view is inert, so focus never lands in it. -->
		<div
			class={[
				'flex w-[200%] items-start transition-transform duration-200 ease-out motion-reduce:transition-none',
				project && '-translate-x-1/2'
			]}
		>
			<div class="w-1/2 min-w-0 px-2 py-3" inert={!!project}>
				{#if outside && org}
					<!-- The org being visited, in place of the switcher's: it isn't one of the viewer's. -->
					<a
						class="mb-2 flex items-center gap-2.5 rounded-lg px-1 py-1 outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-muted"
						href={entityHref(org)}
						aria-current={route === '/(app)/[entity]' ? 'page' : undefined}
						title={org}
					>
						<OrgMark name={orgName ?? org} src={orgAvatar} />
						<span class="min-w-0 flex-1 truncate text-[13.5px] font-medium">{orgName ?? org}</span>
					</a>
				{/if}
				<div class="grid gap-0.5">
					{#if signedIn}
						<a
							class={ITEM}
							href={resolve('/(app)')}
							aria-current={route === '/(app)' ? 'page' : undefined}
						>
							<HouseIcon /> Home
						</a>
					{/if}
					{#if org && !outside}
						<a
							class={ITEM}
							href={entityHref(org)}
							aria-current={route === '/(app)/[entity]' ? 'page' : undefined}
						>
							<ActivityIcon /> Runs
						</a>
						{#if s3}
							<a
								class={ITEM}
								href={datasetsHref(org)}
								aria-current={route?.startsWith('/(app)/datasets/') ? 'page' : undefined}
							>
								<DatabaseIcon /> Datasets
							</a>
							<a
								class={ITEM}
								href={modelsHref(org)}
								aria-current={route?.startsWith('/(app)/models/') ? 'page' : undefined}
							>
								<BoxIcon /> Models
							</a>
							<a
								class={ITEM}
								href={storageHref(org)}
								aria-current={route?.startsWith('/(app)/storage/') ? 'page' : undefined}
							>
								<HardDriveIcon /> Storage
							</a>
						{/if}
					{/if}
				</div>

				{#if org}
					<div class="mx-2 mt-4 border-t border-divider pt-3">
						<div class="flex h-7 items-center gap-1">
							<h2 class="flex-1 text-xs font-medium text-muted-foreground">
								{outside ? 'Public projects' : 'Projects'}
							</h2>
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
							{#if !outside}
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
							{/if}
						</div>
					</div>
					{#if searching}
						<!-- svelte-ignore a11y_autofocus -->
						<input
							class="mx-2 mt-2 h-7 w-[calc(100%-1rem)] rounded-md border border-input bg-background px-2 text-[13px] outline-none placeholder:text-faint focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
							placeholder="Find a project"
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
					<!-- One column no wider than the sidebar, so a long name truncates instead of widening every row. -->
					<ul class="mt-2 grid grid-cols-1 gap-0.5">
						{#each shown as p (p.name)}
							{@const here = page.params.project === p.name}
							<!-- The project's menu shows over the row's end while it's hovered, focused or
							     open, in place of the running dot: both sit in one 24px slot, 4px in. -->
							<li class="group/project relative">
								<a
									class={ITEM}
									href={projectHref(org, p.name)}
									aria-current={here ? 'page' : undefined}
									title={p.description ?? p.name}
								>
									<FolderIcon />
									<span class="min-w-0 flex-1 truncate">{p.name}</span>
									<span class="grid size-6 flex-none place-items-center">
										{#if p.active_run_count}
											<span
												class="size-1.5 animate-live rounded-full bg-state-running text-state-running group-focus-within/project:invisible group-hover/project:invisible motion-reduce:animate-none"
												title="{p.active_run_count} running"
											></span>
										{/if}
									</span>
								</a>
								{#if !outside}
									<div class="absolute inset-y-0 right-1 flex items-center">
										<ProjectMenu
											entity={org}
											project={p}
											from={here ? 'project' : 'list'}
											class="text-muted-foreground opacity-0 group-focus-within/project:opacity-100 group-hover/project:opacity-100 aria-expanded:opacity-100"
										/>
									</div>
								{/if}
							</li>
						{:else}
							<li class="px-2 py-1 text-[13px] text-muted-foreground">
								{query.trim()
									? 'No matching projects'
									: outside
										? 'No public projects'
										: 'No projects yet'}
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
			</div>
			<div
				class="w-1/2 min-w-0 px-2 py-3"
				inert={!project}
				role="group"
				aria-label={shownProject ? `Project ${shownProject}` : undefined}
			>
				{#if org && shownProject}
					{@const scope = { org, project: shownProject }}
					{@const info = projects.find((p) => p.name === shownProject)}
					<!-- Where Home sits in the org's panel, so the way back is under the pointer. -->
					<a
						class={cn(ITEM, 'text-muted-foreground hover:text-foreground')}
						href={signedIn ? resolve('/(app)') : entityHref(org)}
					>
						<ChevronLeftIcon />
						{signedIn ? 'Home' : (orgName ?? org)}
					</a>
					<!-- The project, as the switcher shows the org: a tile and its name, lined up with the links
						below (the tile centred on their icons, the name on their labels). -->
					<div class="mt-3 mb-2 flex items-center gap-2 rounded-lg px-0.5 py-1">
						<span
							class="grid size-7 flex-none place-items-center rounded-lg border border-divider bg-background text-muted-foreground shadow-xs [&_svg]:size-4"
							aria-hidden="true"
						>
							<FolderIcon />
						</span>
						<div class="min-w-0 flex-1">
							<h2
								class="truncate text-[14px] leading-5 font-medium text-foreground"
								title={info?.description ?? shownProject}
							>
								{shownProject}
							</h2>
							{#if info?.active_run_count}
								<p class="flex items-center gap-1.5 text-xs leading-4 text-muted-foreground">
									<span
										class="size-1.5 flex-none animate-live rounded-full bg-state-running text-state-running motion-reduce:animate-none"
									></span>
									<span class="tabular-nums">{info.active_run_count} running</span>
								</p>
							{/if}
						</div>
						{#if info && !outside}
							<ProjectMenu
								entity={org}
								project={info}
								from="project"
								class="text-muted-foreground"
							/>
						{/if}
					</div>
					<div class="grid gap-0.5">
						<a
							class={ITEM}
							href={projectHref(org, shownProject)}
							aria-current={route === projectRoute || route?.startsWith(`${projectRoute}/runs`)
								? 'page'
								: undefined}
						>
							<ActivityIcon /> Runs
						</a>
						{#if groupList}
							<!-- Groups and its children, always expanded (D215). -->
							<a
								class={ITEM}
								href={groupsHref(org, shownProject)}
								aria-current={route === groupRoute ? 'page' : undefined}
							>
								<LayersIcon /> Groups
								<span class="ml-auto pr-1.5 text-xs font-normal text-muted-foreground tabular-nums">
									{groupList.groups.length || ''}
								</span>
							</a>
							<ul class="ml-[17px] grid grid-cols-1 gap-px border-l border-divider pl-[9px]">
								{#each groupList.groups as g (g.id)}
									<!-- The group's menu shows over the count while hovered, focused or open. -->
									<li class="group/grp relative">
										<a
											class={SUB}
											href={groupHref(org, shownProject, g.id)}
											aria-current={route?.startsWith(`${groupRoute}/[group]`) && groupId === g.id
												? 'page'
												: undefined}
											title={g.name}
										>
											<span
												class="size-2 flex-none rounded-[2px]"
												style:background={g.color}
												aria-hidden="true"
											></span>
											<span class="min-w-0 flex-1 truncate">{g.name}</span>
											{#if g.new_count > 0 && !outside}
												<span
													class="size-1.5 flex-none rounded-full bg-state-preempting"
													title="{g.new_count} new {g.new_count === 1 ? 'match' : 'matches'}"
												></span>
												<span class="sr-only">, {g.new_count} new</span>
											{/if}
											<span
												class={[
													'text-xs font-normal text-muted-foreground tabular-nums',
													!outside &&
														'group-focus-within/grp:invisible group-hover/grp:invisible group-has-aria-expanded/grp:invisible'
												]}
											>
												{g.run_count}
											</span>
										</a>
										{#if !outside}
											<div class="absolute inset-y-0 right-0.5 flex items-center">
												<GroupMenu
													entity={org}
													project={shownProject}
													group={g}
													class="text-muted-foreground opacity-0 group-focus-within/grp:opacity-100 group-hover/grp:opacity-100 aria-expanded:opacity-100"
												/>
											</div>
										{/if}
									</li>
								{/each}
								<!-- Only while some runs are in no group: none left, nothing to show. -->
								{#if groupList.ungrouped_count > 0}
									<li>
										<a
											class={cn(SUB, 'text-muted-foreground')}
											href={ungroupedHref(org, shownProject)}
										>
											<span
												class="size-2 flex-none rounded-full border-[1.5px] border-faint"
												aria-hidden="true"
											></span>
											<span class="min-w-0 flex-1 truncate">Ungrouped runs</span>
											<span class="text-xs tabular-nums">{groupList.ungrouped_count}</span>
										</a>
									</li>
								{/if}
								{#if !outside}
									<li>
										<a
											class={cn(SUB, 'text-muted-foreground aria-[current=page]:text-foreground')}
											href={newGroupHref(org, shownProject)}
											aria-current={route === `${groupRoute}/new` ? 'page' : undefined}
										>
											<PlusIcon class="-ml-0.5 size-3 flex-none" />
											<span class="min-w-0 flex-1 truncate">New group</span>
										</a>
									</li>
									{#if suggestions.length}
										<li class="mx-1.5 my-1 h-px bg-divider" role="presentation"></li>
									{/if}
									{#each suggestions as s (s.signature)}
										<li class="group/suggestion relative">
											<a
												class={cn(SUB, 'pr-7 text-muted-foreground')}
												href={suggestionHref(org, s)}
												title="Suggested from {s.source === 'wandb_group'
													? 'a W&B group'
													: 'runs whose configs differ in a few keys'}: {s.name}"
											>
												<span
													class="size-2 flex-none rounded-[2px] border-[1.5px] border-dashed border-state-preempting"
													aria-hidden="true"
												></span>
												<span class="min-w-0 flex-1 truncate">{s.name}</span>
												<span
													class="rounded-[4px] border border-border px-1 text-[11px] leading-4 group-focus-within/suggestion:invisible group-hover/suggestion:invisible"
												>
													Suggested
												</span>
											</a>
											<Button
												variant="ghost"
												size="icon-xs"
												class="absolute top-0.5 right-0.5 text-muted-foreground opacity-0 group-focus-within/suggestion:opacity-100 group-hover/suggestion:opacity-100"
												aria-label="Dismiss suggestion {s.name}"
												title="Dismiss"
												onclick={() => dismiss(org, s)}
											>
												<XIcon />
											</Button>
										</li>
									{/each}
								{/if}
							</ul>
						{/if}
						<!-- A project's datasets and models are its org's members' only (D165). -->
						{#if s3 && !outside}
							<a
								class={ITEM}
								href={datasetsHref(scope)}
								aria-current={route?.startsWith(`${projectRoute}/datasets`) ? 'page' : undefined}
							>
								<DatabaseIcon /> Datasets
							</a>
							<a
								class={ITEM}
								href={modelsHref(scope)}
								aria-current={route?.startsWith(`${projectRoute}/models`) ? 'page' : undefined}
							>
								<BoxIcon /> Models
							</a>
						{/if}
					</div>
				{/if}
			</div>
		</div>
	</nav>

	<div class="flex-none border-t border-divider p-2">
		{#if signedIn}
			<!-- From outside the org, the switcher names only the viewer's own. -->
			<WorkspaceSwitcher org={outside ? null : org} {orgs} stay={!inOrg} />
		{:else}
			<Button variant="outline" class="w-full" href={loginHref(page.url)} data-sveltekit-reload>
				<LogInIcon /> Sign in
			</Button>
		{/if}
	</div>
</div>

{#if org && !outside}<ProjectDialog bind:open={creating} entity={org} />{/if}
