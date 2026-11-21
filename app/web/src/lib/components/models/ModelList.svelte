<script lang="ts">
	import BoxIcon from '@lucide/svelte/icons/box';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import { untrack } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { Button } from '#lib/components/ui/button/index.js';
	import {
		folderApi,
		modelCall,
		modelsHref,
		pullSnippet,
		pushSnippet,
		saveSnippet
	} from '#lib/models.ts';
	import { nameOf, parentOf } from '#lib/datasets.ts';
	import type { Scope } from '#lib/scope.ts';
	import type { Selection } from '#lib/selection.svelte.ts';
	import type { ModelEntry, ModelFolder, ModelMove } from '#lib/types.ts';
	import { bytes, count, dateTime, relativeTime } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import RowCheckbox from '../RowCheckbox.svelte';
	import RowMenu from '../RowMenu.svelte';
	import DeleteModelDialog from './DeleteModelDialog.svelte';
	import DeleteModelsDialog from './DeleteModelsDialog.svelte';
	import MoveModelsDialog from './MoveModelsDialog.svelte';
	import RenameModelDialog from './RenameModelDialog.svelte';

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
	let rows = $derived(folder.models);
	let folders = $derived(folder.folders);
	let cursor = $derived(folder.cursor);
	let loading = $state(false);
	let failed = $state<string | null>(null);
	$effect.pre(() => {
		if (selection) selection.keys = rows.map((m) => m.name);
	});

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
			one = rows.find((m) => m.name === name) ?? { name, model: refOf(name), versions: 0 };
		});
	});

	function gone(names: string[]) {
		const out = new Set(names);
		rows = rows.filter((m) => !out.has(m.name));
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
		rows = rows.map((m) => (m.name === from ? { ...m, name: to, model: refOf(to) } : m));
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
			rows = [...rows, ...page.models];
			folders = [...folders, ...page.folders];
			cursor = page.cursor;
		} catch (e) {
			failed = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	/** How the snippets name an example model: by `org/` for global, with `--project` else. */
	const example = $derived(scope.project === null ? `${scope.org}/my-model` : 'my-model');
</script>

{#if !rows.length && !folders.length}
	<div class="max-w-[68ch]">
		{#if scope.project !== null}
			<p class="text-[15px]">No models in {scope.project} yet.</p>
			<p class="mt-1 text-muted-foreground">
				Save a model from Python and it shows up here. It’s deleted with the project.
			</p>
		{:else}
			<p class="text-[15px]">No models yet.</p>
			<p class="mt-1 text-muted-foreground">Save a model from Python and it shows up here.</p>
		{/if}
		<div class="mt-3"><CopyBlock code={saveSnippet(scope)} /></div>
		<p class="mt-3 text-[13px] text-muted-foreground">
			Or push a folder with <code class="font-mono text-[0.9em]">{pushSnippet(scope)}</code>, and
			load it back with
			<code class="font-mono text-[0.9em]">{pullSnippet(example, scope.project)}</code>. Names can
			have folders, like <code class="font-mono text-[0.9em]">sweeps/lr-3e-4</code>.
		</p>
	</div>
{:else}
	<div class="grid gap-3">
		<table class="group/table w-full table-fixed border-collapse text-[13.5px]">
			<thead>
				<tr class="h-10 border-b text-left text-xs font-medium text-muted-foreground">
					{#if selection}
						<th class="w-8 pl-2"><RowCheckbox {selection} /></th>
					{/if}
					<th class="px-2 font-medium">Name</th>
					<th class="hidden w-28 px-2 font-medium sm:table-cell">Latest</th>
					<th class="w-28 px-2 font-medium">Primary</th>
					<th class="hidden w-28 px-2 font-medium sm:table-cell">Size</th>
					<th class="w-32 px-2 font-medium">Updated</th>
					{#if selection}<th class="w-9"><span class="sr-only">Actions</span></th>{/if}
				</tr>
			</thead>
			<tbody>
				{#each rows as m (`m:${m.name}`)}
					{@const on = !!selection?.has(m.name)}
					<tr
						class={[
							'group/row h-10 border-b border-divider',
							on ? 'bg-selection/45' : 'hover:bg-muted'
						]}
						aria-selected={selection ? on : undefined}
					>
						{#if selection}
							<td class="pl-2"><RowCheckbox {selection} key={m.name} name={nameOf(m.name)} /></td>
						{/if}
						<td class="truncate px-2">
							<a
								class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
								href={modelsHref(scope, m.name)}
							>
								<BoxIcon class="size-4 flex-none text-muted-foreground" />
								<span class="truncate">{nameOf(m.name)}</span>
							</a>
						</td>
						<td class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell">
							{m.latest === null ? '—' : `Version ${m.latest}`}
						</td>
						<td class="px-2 text-muted-foreground tabular-nums">
							{m.primary === null ? '—' : `Version ${m.primary}`}
						</td>
						<td class="hidden px-2 text-muted-foreground tabular-nums sm:table-cell">
							{m.size == null ? '—' : bytes(m.size)}
						</td>
						<td class="px-2 text-muted-foreground tabular-nums" title={dateTime(m.updated_at)}>
							{relativeTime(m.updated_at, now)}
						</td>
						{#if selection}
							<td class="pr-1 text-right">
								<RowMenu
									name={nameOf(m.name)}
									onrename={() => selection.act('rename', [m.name])}
									onmove={() => selection.act('move', [m.name])}
									ondelete={() => selection.act('delete', [m.name])}
								/>
							</td>
						{/if}
					</tr>
				{/each}
				{#each folders as f (`f:${f.path}`)}
					<tr class="h-10 border-b border-divider hover:bg-muted">
						{#if selection}<td></td>{/if}
						<td class="truncate px-2">
							<a
								class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
								href={modelsHref(scope, f.path)}
							>
								<FolderIcon class="size-4 flex-none text-muted-foreground" />
								<span class="truncate">{f.name}/</span>
							</a>
						</td>
						<td class="hidden px-2 text-muted-foreground sm:table-cell">—</td>
						<td class="px-2 text-muted-foreground tabular-nums">{count(f.models, 'model')}</td>
						<td class="hidden px-2 text-muted-foreground sm:table-cell">—</td>
						<td class="px-2 text-muted-foreground">—</td>
						{#if selection}<td></td>{/if}
					</tr>
				{/each}
			</tbody>
		</table>
		{#if failed}
			<p class="text-[13px] text-destructive" role="alert">{failed}</p>
		{/if}
		{#if cursor}
			<div>
				<Button variant="outline" size="sm" disabled={loading} onclick={more}>
					{loading ? 'Loading…' : 'Load more'}
				</Button>
			</div>
		{/if}
	</div>
{/if}

{#if selection}
	{@const many = selection.targets.length > 1}
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
		{scope}
		name={one.name}
		onrenamed={(to) => renamed(one.name, to)}
	/>
{/if}
