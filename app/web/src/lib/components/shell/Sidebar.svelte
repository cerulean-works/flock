<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import BoxIcon from '@lucide/svelte/icons/box';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import FolderOpenIcon from '@lucide/svelte/icons/folder-open';
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
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import * as Popover from '#lib/components/ui/popover/index.js';
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
	 * The project in view (D169): it opens in place in the Projects list, with its runs, groups,
	 * datasets and models nested under it. The sidebar never changes shape under the pointer.
	 */
	const project = $derived(
		org && page.params.project && route?.startsWith('/(app)/[entity]/[project]')
			? page.params.project
			: null
	);
	const projectRoute = '/(app)/[entity]/[project]';

	// The project's groups and suggestions (D215), from its layout's load. Suggestions arrive as a
	// promise the layout doesn't wait for: they're shown once it resolves, and a different
	// project's are cleared at once rather than shown under this one.
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
	const groupList = $derived(
		project ? ((page.data.groups as GroupList | null | undefined) ?? null) : null
	);
	const suggestions = $derived(project && streamed.project === project ? streamed.list : []);
	const groupRoute = `${projectRoute}/groups`;
	const groupId = $derived(Number(page.params.group));

	/** Dismissing a suggestion remembers it (D219): it isn't offered again unless its runs change. */
	async function dismiss(org: string, s: Suggestion) {
		if (!project) return;
		try {
			await groupsCall(groupsApi(org, project, 'suggestions/dismiss'), {
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
		newGroupHref(org, project!, {
			runs: s.filter ? [] : s.runs,
			filters: (s.filter && fromFlockFilter(s.filter)) || [],
			name: s.name
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

	// Every row is 28px of 13px text, led by a 24px slot with its 16px icon centred, so icons and
	// labels each start on one edge. Nested rows step in by one slot, under their parent's label.
	const ITEM =
		'flex h-7 items-center gap-2 rounded-md px-1 text-[13px] text-sidebar-foreground outline-none hover:bg-sidebar-accent focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:bg-sidebar-accent aria-[current=page]:font-medium [&_svg]:size-4 [&_svg]:flex-none [&_svg]:stroke-[1.75] [&_svg]:text-muted-foreground [&>svg]:mx-1';
	// A group's color square (or Ungrouped's hollow one) in the same 24px slot.
	const SQUARE = 'mx-[8px] size-2 flex-none rounded-[2px]';
	// The muted count, or live mark, at a row's right end: 24px wide, where its menu shows on hover.
	const END =
		'grid h-6 min-w-6 flex-none place-items-center px-1 text-xs font-normal text-muted-foreground tabular-nums';

	let suggesting = $state(false);
</script>

<!-- The project open in the list: its runs, groups, datasets and models, one step in. -->
{#snippet children(org: string, project: string)}
	{@const scope = { org, project }}
	<div class="ml-6 grid gap-px py-px" role="group" aria-label="Project {project}">
		<a
			class={ITEM}
			href={projectHref(org, project)}
			aria-current={route === projectRoute || route?.startsWith(`${projectRoute}/runs`)
				? 'page'
				: undefined}
		>
			<ActivityIcon /> Runs
		</a>
		{#if groupList}
			<!-- Groups and its children, always expanded (D215). -->
			<div class="group/groups relative">
				<a
					class={ITEM}
					href={groupsHref(org, project)}
					aria-current={route === groupRoute ? 'page' : undefined}
				>
					<LayersIcon />
					<span class="flex-1">Groups</span>
				</a>
				{#if !outside}
					<Button
						variant="ghost"
						size="icon-xs"
						class="absolute top-0.5 right-0.5 text-muted-foreground opacity-0 group-focus-within/groups:opacity-100 group-hover/groups:opacity-100 hover:bg-background/60"
						href={newGroupHref(org, project)}
						aria-label="New group"
						aria-current={route === `${groupRoute}/new` ? 'page' : undefined}
						{@attach hint('New group')}
					>
						<PlusIcon />
					</Button>
				{/if}
			</div>
			<ul class="ml-6 grid grid-cols-1 gap-px">
				{#each groupList.groups as g (g.id)}
					<!-- The group's menu shows over the count while hovered, focused or open. -->
					<li class="group/grp relative">
						<a
							class={ITEM}
							href={groupHref(org, project, g.id)}
							aria-current={route?.startsWith(`${groupRoute}/[group]`) && groupId === g.id
								? 'page'
								: undefined}
							{@attach hint(g.name)}
						>
							<span class={SQUARE} style:background={g.color} aria-hidden="true"></span>
							<span class="min-w-0 flex-1 truncate">{g.name}</span>
							{#if g.new_count > 0 && !outside}
								<span
									class="size-1.5 flex-none rounded-full bg-state-preempting"
									{@attach hint(`${g.new_count} new ${g.new_count === 1 ? 'match' : 'matches'}`)}
								></span>
								<span class="sr-only">, {g.new_count} new</span>
							{/if}
							<span
								class={[
									END,
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
									{project}
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
						<a class={cn(ITEM, 'text-muted-foreground')} href={ungroupedHref(org, project)}>
							<span class={cn(SQUARE, 'border-[1.5px] border-faint')} aria-hidden="true"></span>
							<span class="min-w-0 flex-1 truncate">Ungrouped runs</span>
							<span class={END}>{groupList.ungrouped_count}</span>
						</a>
					</li>
				{/if}
				{#if !outside && suggestions.length}
					<!-- Suggestions fold into one row, opened as a list beside the sidebar. -->
					<li>
						<Popover.Root bind:open={suggesting}>
							<Popover.Trigger
								class={cn(ITEM, 'w-full text-muted-foreground aria-expanded:bg-sidebar-accent')}
							>
								<SparklesIcon />
								<span class="min-w-0 flex-1 truncate text-left">Suggested</span>
								<span class={END}>{suggestions.length}</span>
							</Popover.Trigger>
							<Popover.Content side="right" align="start" class="w-72 gap-0 p-1">
								<ul class="grid gap-px" aria-label="Suggested groups">
									{#each suggestions as s (s.signature)}
										<li class="group/suggestion relative">
											<a
												class="flex min-h-8 items-center gap-2 rounded-md px-2 py-1 pr-8 text-[13px] outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
												href={suggestionHref(org, s)}
												onclick={() => (suggesting = false)}
												{@attach hint(
													s.source === 'wandb_group'
														? 'From a W&B group'
														: 'Runs whose configs differ in a few keys'
												)}
											>
												<span
													class="size-2 flex-none rounded-[2px] border-[1.5px] border-dashed border-faint"
													aria-hidden="true"
												></span>
												<span class="min-w-0 flex-1 truncate">{s.name}</span>
											</a>
											<Button
												variant="ghost"
												size="icon-xs"
												class="absolute top-1 right-1 text-muted-foreground opacity-0 group-focus-within/suggestion:opacity-100 group-hover/suggestion:opacity-100"
												aria-label="Dismiss suggestion {s.name}"
												{@attach hint('Dismiss')}
												onclick={() => dismiss(org, s)}
											>
												<XIcon />
											</Button>
										</li>
									{/each}
								</ul>
							</Popover.Content>
						</Popover.Root>
					</li>
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
{/snippet}

<div class="flex h-full flex-col bg-sidebar text-sidebar-foreground">
	<!-- 40px, level with the content's header row. The mark sits in the rows' icon slot and the
	     name on their labels' edge. -->
	<div class="flex h-10 flex-none items-center gap-1 px-2">
		<a
			href={resolve('/(app)')}
			class="flex h-7 min-w-0 flex-1 items-center gap-2 rounded-md px-1 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
		>
			<LogoMark class="mx-1 size-4" />
			<span class="text-[14px] font-semibold tracking-[-0.01em]">Flock</span>
		</a>
		{#if oncollapse}
			<Button
				variant="ghost"
				size="icon-sm"
				class="flex-none text-muted-foreground hover:bg-sidebar-accent"
				aria-label="Hide sidebar"
				{@attach hint('Hide sidebar  [')}
				onclick={oncollapse}
			>
				<PanelLeftCloseIcon />
			</Button>
		{/if}
	</div>

	<nav class="min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-2 pt-1 pb-3" aria-label="Main">
		<div class="grid gap-px">
			{#if outside && org}
				<!-- The org being visited, in place of the switcher's: it isn't one of the viewer's. -->
				<a
					class={ITEM}
					href={entityHref(org)}
					aria-current={route === '/(app)/[entity]' ? 'page' : undefined}
				>
					<OrgMark
						name={orgName ?? org}
						src={orgAvatar}
						class="mx-1 size-4 rounded-[4px] text-[9px]"
					/>
					<span class="min-w-0 flex-1 truncate">{orgName ?? org}</span>
				</a>
			{/if}
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
			<!-- Its actions show while the section is hovered or focused, as Linear's do. -->
			<div class="group/section mt-5 flex h-7 items-center gap-0.5 pr-0.5 pl-2">
				<h2 class="flex-1 text-xs font-medium text-muted-foreground">
					{outside ? 'Public projects' : 'Projects'}
				</h2>
				<Button
					variant="ghost"
					size="icon-xs"
					class={[
						'text-muted-foreground opacity-0 group-focus-within/section:opacity-100 group-hover/section:opacity-100 hover:bg-sidebar-accent',
						searching && 'bg-sidebar-accent opacity-100'
					]}
					aria-label="Find a project"
					{@attach hint('Find a project')}
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
						class="text-muted-foreground opacity-0 group-focus-within/section:opacity-100 group-hover/section:opacity-100 hover:bg-sidebar-accent"
						aria-label="New project"
						{@attach hint('New project')}
						onclick={() => (creating = true)}
					>
						<PlusIcon />
					</Button>
				{/if}
			</div>
			{#if searching}
				<!-- svelte-ignore a11y_autofocus -->
				<input
					class="mt-1 mb-1 h-7 w-full rounded-md border border-input bg-background px-2 text-[13px] outline-none placeholder:text-faint focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
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
			<ul class="grid grid-cols-1 gap-px">
				{#each shown as p (p.name)}
					{@const here = project === p.name}
					<!-- The project's menu shows over the row's end while it's hovered, focused or open,
					     in place of its live count. -->
					<li>
						<div class="group/project relative">
							<a
								class={ITEM}
								href={projectHref(org, p.name)}
								class:font-medium={here}
								{@attach hint(p.description)}
							>
								<!-- The project in view shows open, its pages nested under it. -->
								{#if here}<FolderOpenIcon />{:else}<FolderIcon />{/if}
								<span class="min-w-0 flex-1 truncate">{p.name}</span>
								{#if p.active_run_count}
									<span
										class={cn(
											END,
											'gap-1.5 group-focus-within/project:invisible group-hover/project:invisible',
											!outside && 'group-has-aria-expanded/project:invisible'
										)}
										{@attach hint(`${p.active_run_count} running`)}
									>
										<span class="flex items-center gap-1.5">
											<span class="size-1.5 rounded-full bg-state-running" aria-hidden="true"
											></span>
											{p.active_run_count}
										</span>
									</span>
									<span class="sr-only">, {p.active_run_count} running</span>
								{/if}
							</a>
							{#if !outside}
								<div class="absolute inset-y-0 right-0.5 flex items-center">
									<ProjectMenu
										entity={org}
										project={p}
										from={here ? 'project' : 'list'}
										class="text-muted-foreground opacity-0 group-focus-within/project:opacity-100 group-hover/project:opacity-100 aria-expanded:opacity-100"
									/>
								</div>
							{/if}
						</div>
						{#if here}{@render children(org, p.name)}{/if}
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
			{#if project && !shown.some((p) => p.name === project)}
				<!-- A project beyond the shown ones (or outside the org's list): still open in place. -->
				<div class="mt-1 grid grid-cols-1 gap-px">
					<a
						class={ITEM}
						href={projectHref(org, project)}
						aria-current={route === projectRoute ? 'page' : undefined}
					>
						<FolderOpenIcon />
						<span class="min-w-0 flex-1 truncate">{project}</span>
					</a>
					{@render children(org, project)}
				</div>
			{/if}
		{/if}
	</nav>

	<div class="flex-none border-t border-divider p-2">
		{#if signedIn}
			<!-- The org and who is signed in. From outside the org, it names only the viewer's own. -->
			<WorkspaceSwitcher org={outside ? null : org} {orgs} stay={!inOrg} />
		{:else}
			<Button variant="outline" class="w-full" href={loginHref(page.url)} data-sveltekit-reload>
				<LogInIcon /> Sign in
			</Button>
		{/if}
	</div>
</div>

{#if org && !outside}<ProjectDialog bind:open={creating} entity={org} />{/if}
