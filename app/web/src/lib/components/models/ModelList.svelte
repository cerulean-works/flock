<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import BoxIcon from '@lucide/svelte/icons/box';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import { untrack } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import { folderApi, modelCall, modelsHref, pushSnippet } from '#lib/models.ts';
	import { nameOf, parentOf } from '#lib/datasets.ts';
	import type { Scope } from '#lib/scope.ts';
	import { modelSnippet } from '#lib/snippets.ts';
	import type { Selection } from '#lib/selection.svelte.ts';
	import type { ModelEntry, ModelFolder, ModelMove } from '#lib/types.ts';
	import { bytes, count, dateTime, relativeTime } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import DataTable, { type Column, ROW_LINK } from '../DataTable.svelte';
	import EmptyState from '../EmptyState.svelte';
	import RowMenu from '../RowMenu.svelte';
	import DeleteModelDialog from './DeleteModelDialog.svelte';
	import DeleteModelsDialog from './DeleteModelsDialog.svelte';
	import FolderDialog from './FolderDialog.svelte';
	import { openMenuTrigger } from '../AnchoredForm.svelte';
	import MoveModelsDialog from './MoveModelsDialog.svelte';
	import RenameModelDialog from './RenameModelDialog.svelte';

	// Folders have no versions, size or update time: their count of models goes under Primary.
	const COLUMNS: Column[] = [
		{ id: 'name', label: 'Name' },
		{ id: 'latest', label: 'Latest', size: 'fit', from: 'sm' },
		{ id: 'primary', label: 'Primary', size: 'fit' },
		{ id: 'size', label: 'Size', from: 'sm', align: 'end' },
		{ id: 'updated', label: 'Updated', align: 'end' }
	];

	interface Props {
		scope: Scope;
		/** The folder shown: empty for the scope's root (D158). */
		path: string;
		/** Its first page, from the server. */
		folder: ModelFolder;
		now: number;
		/**
		 * Models checked, for members and admins (D130), whose rows also get a menu (D133). The
		 * page shows the selection bar, whose Move and Delete open this list's dialogs. Folders
		 * aren't checked: they're only where models are.
		 */
		selection?: Selection;
		/** The org's projects, for the Move dialog's scope picker (D166). */
		projects?: readonly string[];
	}

	let { scope, path, folder, now, selection, projects = [] }: Props = $props();

	// The server's first page, then pages loaded here with "Load more", with models deleted or
	// moved here taken out until it's read again. Models come first in each page, then folders.
	let models = $derived(folder.models);
	let folders = $derived(folder.folders);
	// One table: the models, then the folders.
	type Row =
		{ kind: 'model'; m: ModelEntry } | { kind: 'folder'; f: ModelFolder['folders'][number] };
	const rows: Row[] = $derived([
		...models.map((m) => ({ kind: 'model' as const, m })),
		...folders.map((f) => ({ kind: 'folder' as const, f }))
	]);
	let cursor = $derived(folder.cursor);
	let loading = $state(false);
	let failed = $state<string | null>(null);
	$effect.pre(() => {
		if (selection) selection.keys = models.map((m) => m.name);
	});

	/** The folder whose ⋯ menu was used, and what for. */
	let folderAction = $state<'rename' | 'move' | 'delete' | null>(null);
	/** The ⋯ menu a rename opened from. */
	let renameAnchor = $state<HTMLElement | null>(null);
	let folderOf = $state.raw<{ path: string; models: number }>({ path: '', models: 0 });
	function onFolder(f: { path: string; models: number }, action: 'rename' | 'move' | 'delete') {
		renameAnchor = openMenuTrigger();
		folderOf = f;
		folderAction = action;
	}

	/** How refs name a model of this scope (D162). */
	const refOf = (name: string) =>
		scope.project === null ? `${scope.org}/${name}` : `${scope.project}:${name}`;

	/** The model a dialog for one model acts on, as it was when opened. */
	let one = $state.raw<Pick<ModelEntry, 'name' | 'model' | 'versions'>>({
		name: '',
		model: '',
		versions: 0
	});
	$effect.pre(() => {
		const name = selection?.targets[0] ?? '';
		untrack(() => {
			one = models.find((m) => m.name === name) ?? { name, model: refOf(name), versions: 0 };
		});
	});

	function gone(names: string[]) {
		const out = new Set(names);
		models = models.filter((m) => !out.has(m.name));
		selection?.drop(names);
	}

	async function deleted(names: string[]) {
		gone(names);
		await invalidateAll();
	}

	/** Models renamed or moved: out of the list, read again if one landed in this folder. */
	async function landed(from: string[], to: string[], here = true) {
		gone(from);
		if (here && to.some((t) => parentOf(t) === path)) await invalidateAll();
	}

	/** A model renamed in this folder keeps its row, under the new name; elsewhere it leaves. */
	async function renamed(from: string, to: string) {
		if (parentOf(to) !== path) return landed([from], [to]);
		models = models.map((m) => (m.name === from ? { ...m, name: to, model: refOf(to) } : m));
		selection?.drop([from]);
		await invalidateAll();
	}

	const moved = (results: ModelMove[], to: Scope) => {
		const done = results.filter((r) => r.moved);
		return landed(
			done.map((r) => r.name),
			done.map((r) => r.to),
			to.project === scope.project
		);
	};

	async function more() {
		if (!cursor) return;
		loading = true;
		failed = null;
		try {
			const page = await modelCall<ModelFolder>(folderApi(scope, path, cursor));
			models = [...models, ...page.models];
			folders = [...folders, ...page.folders];
			cursor = page.cursor;
		} catch (e) {
			failed = (e as Error).message;
		} finally {
			loading = false;
		}
	}
</script>

{#if !rows.length && path}
	<EmptyState variant="onboarding" title="This folder is empty">
		{#snippet lead()}
			Move a model here, or save one under <code class="font-mono text-[0.9em]">{path}/</code>.
		{/snippet}
	</EmptyState>
{:else if !rows.length}
	<EmptyState
		variant="onboarding"
		title={scope.project === null ? 'No models yet' : `No models in ${scope.project} yet`}
	>
		{#snippet lead()}Save a model from Python and it shows up here.{/snippet}
		<CopyBlock lang="python" code={modelSnippet(scope)} />
		{#snippet actions()}
			<span class="w-full">
				You can also push a folder with
				<code class="font-mono text-[0.9em] whitespace-nowrap">{pushSnippet(scope)}</code>.
			</span>
		{/snippet}
	</EmptyState>
{:else}
	<DataTable
		columns={COLUMNS}
		{rows}
		key={(r) => (r.kind === 'model' ? r.m.name : `${r.f.path}/`)}
		label="Models"
		selection={selection ?? null}
		selectable={(r) => r.kind === 'model'}
		name={(r) => (r.kind === 'model' ? nameOf(r.m.name) : r.f.name)}
		menu={selection ? rowMenu : undefined}
		dense
		footer={cursor || failed ? footer : undefined}
	>
		{#snippet lead(r)}
			{#if r.kind === 'model'}<BoxIcon />{:else}<FolderIcon />{/if}
		{/snippet}
		{#snippet cell(r, column)}
			{#if column === 'name'}
				<a
					class={['block truncate underline-offset-3 hover:text-link hover:underline', ROW_LINK]}
					href={modelsHref(scope, r.kind === 'model' ? r.m.name : r.f.path)}
				>
					{r.kind === 'model' ? nameOf(r.m.name) : r.f.name}
				</a>
			{:else if r.kind === 'folder'}
				<!-- A folder has only its count of models, under Primary. -->
				{#if column === 'primary'}
					<span class="text-muted-foreground tabular-nums">{count(r.f.models, 'model')}</span>
				{/if}
			{:else if column === 'latest'}
				<span class="text-muted-foreground">
					{r.m.latest === null ? '' : `Version ${r.m.latest}`}
				</span>
			{:else if column === 'primary'}
				<span class="text-muted-foreground">
					{r.m.primary === null ? '' : `Version ${r.m.primary}`}
				</span>
			{:else if column === 'size'}
				<span class="text-muted-foreground">{r.m.size == null ? '' : bytes(r.m.size)}</span>
			{:else}
				<span class="text-muted-foreground" {@attach hint(dateTime(r.m.updated_at))}>
					{relativeTime(r.m.updated_at, now)}
				</span>
			{/if}
		{/snippet}
	</DataTable>
{/if}

{#snippet rowMenu(r: Row)}
	{#if selection && r.kind === 'model'}
		<RowMenu
			name={nameOf(r.m.name)}
			onrename={() => {
				renameAnchor = openMenuTrigger();
				selection.act('rename', [r.m.name]);
			}}
			onmove={() => selection.act('move', [r.m.name])}
			ondelete={() => selection.act('delete', [r.m.name])}
		/>
	{:else if r.kind === 'folder'}
		<RowMenu
			name={r.f.name}
			onrename={() => onFolder(r.f, 'rename')}
			onmove={() => onFolder(r.f, 'move')}
			ondelete={() => onFolder(r.f, 'delete')}
		/>
	{/if}
{/snippet}

{#snippet footer()}
	{#if cursor}
		<Button variant="outline" onclick={more} pending={loading}>Load more</Button>
	{/if}
	{#if failed}
		<span class="text-[13px] text-destructive" role="alert">{failed}</span>
	{/if}
{/snippet}

{#if selection}
	{@const many = selection.targets.length > 1}
	<FolderDialog
		bind:open={() => folderAction !== null, (o) => o || (folderAction = null)}
		action={folderAction ?? 'rename'}
		anchor={renameAnchor}
		{scope}
		path={folderOf.path}
		models={folderOf.models}
		{projects}
	/>
	<MoveModelsDialog
		bind:open={() => selection.action === 'move', (o) => o || selection.close('move')}
		{scope}
		from={path}
		names={selection.targets}
		{projects}
		onmoved={moved}
	/>
	<DeleteModelsDialog
		bind:open={() => many && selection.action === 'delete', (o) => o || selection.close('delete')}
		{scope}
		names={selection.targets}
		ondeleted={deleted}
	/>
	<DeleteModelDialog
		bind:open={() => !many && selection.action === 'delete', (o) => o || selection.close('delete')}
		{scope}
		info={one}
		ondeleted={() => deleted([one.name])}
	/>
	<RenameModelDialog
		bind:open={() => selection.action === 'rename', (o) => o || selection.close('rename')}
		anchor={renameAnchor}
		{scope}
		name={one.name}
		onrenamed={(to) => renamed(one.name, to)}
	/>
{/if}
