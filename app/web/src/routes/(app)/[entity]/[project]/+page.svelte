<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { goto, refreshAll } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import LiveStatus from '#lib/components/LiveStatus.svelte';
	import PublicBadge from '#lib/components/PublicBadge.svelte';
	import RunView from '#lib/components/RunView.svelte';
	import { clearRunPages } from '#lib/data/cache.ts';
	import type { ProjectEvent } from '#lib/data/live.ts';
	import { wandbSnippet } from '#lib/snippets.ts';
	import { lastView } from '#lib/workspace/project-view.svelte.ts';
	import { leftWorkspace, rememberFilters, showingProject } from '#lib/workspace/remembered.ts';
	import { entityHref, workspaceHrefWith } from '#lib/workspace/url.ts';
	import { Workspace, setWorkspace } from '#lib/workspace/workspace.svelte.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const ws = new Workspace(
		() => data.meta,
		() => data.runs,
		() => data.people
	);
	setWorkspace(ws);

	// Lets performance tooling measure navigation-start → interactive.
	onMount(() => performance.mark('workspace:hydrated'));

	// Opened from the server (no load ran here to bring back the filters it was left with): the
	// same once hydrated.
	onMount(() => {
		const { entity, project } = page.params as { entity: string; project: string };
		if (showingProject(entity, project)) return;
		const filters = rememberFilters(entity, project, page.url.searchParams);
		if (filters)
			void goto(workspaceHrefWith(entity, project, page.url.search, { filters, page: null }), {
				replaceState: true
			});
	});
	onDestroy(leftWorkspace);

	// The viewer's panel arrangement for this project, once hydrated (the server can't know it).
	$effect(() => {
		void [ws.meta.entity, ws.meta.project];
		untrack(() => ws.restoreLayout());
	});

	// Someone else renamed or deleted the project: follow it, or leave.
	// The same toast id as the dialogs' own, so a change made here isn't announced twice.
	function onproject(e: ProjectEvent) {
		const { entity, project } = ws.meta;
		if (e.visibility === 'private') {
			// Made private while an outsider watched: reading it again sends them to sign in, or
			// to the 404 page.
			toast(`${project} is no longer public`, { id: 'project' });
			clearRunPages();
			void refreshAll();
			return;
		}
		if (e.deleted) {
			toast(`${project} was deleted`, { id: 'project' });
			void goto(entityHref(entity), { replaceState: true });
			return;
		}
		const to = e.renamed_to ?? project;
		toast(`Project renamed to ${to}`, { id: 'project' });
		clearRunPages();
		void goto(workspaceHrefWith(entity, to, page.url.search, {}), {
			replaceState: true
		});
	}

	/** Outside the org: read-only, with no run actions (D116). */
	const readonly = $derived(data.shell.access === 'public');
	const description = $derived(data.project?.description ?? null);
	const visibility = $derived(data.project?.visibility ?? 'private');
</script>

{#snippet publicBadge()}<PublicBadge what="its runs" />{/snippet}

<svelte:head>
	<title>{ws.meta.project} · {data.shell.orgName ?? ws.meta.entity}</title>
</svelte:head>

<RunView
	title="Runs"
	subtitleTitle={description ?? undefined}
	badges={visibility === 'public' ? publicBadge : undefined}
	initialView={lastView(data.shell.projectView)}
	remember
	evalPreset
	{onproject}
	{readonly}
	nameW={data.shell.nameW}
	tableW={data.shell.tableW}
	stacked={data.shell.stacked}
	tableH={data.shell.tableH}
>
	{#snippet actions()}
		<LiveStatus />
	{/snippet}
	{#snippet empty()}
		{#if readonly}
			<EmptyState
				variant="onboarding"
				class="empty absolute inset-0 z-4 overflow-y-auto bg-background"
				title="No runs yet"
			>
				{#snippet lead()}Nothing has been logged to this project.{/snippet}
			</EmptyState>
		{:else}
			<EmptyState
				variant="onboarding"
				class="empty absolute inset-0 z-4 overflow-y-auto bg-background"
				title="No runs yet"
			>
				{#snippet lead()}Log one from Python with the wandb SDK and it shows up here.{/snippet}
				<CopyBlock lang="python" code={wandbSnippet(ws.meta.entity, ws.meta.project)} />
				{#snippet actions()}
					<span>
						Sign in with your wandb key, or
						<a
							class="text-link underline-offset-3 hover:underline"
							href="{resolve('/(app)/account')}#keys">create one</a
						>.
					</span>
				{/snippet}
			</EmptyState>
		{/if}
	{/snippet}
</RunView>
