<script lang="ts">
	import BoxIcon from '@lucide/svelte/icons/box';
	import { untrack } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { modelsHref, pullSnippet } from '#lib/models.ts';
	import type { Selection } from '#lib/selection.svelte.ts';
	import type { ModelEntry } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';
	import CopyBlock from '../CopyBlock.svelte';
	import RowCheckbox from '../RowCheckbox.svelte';
	import RowMenu from '../RowMenu.svelte';
	import DeleteModelDialog from './DeleteModelDialog.svelte';
	import DeleteModelsDialog from './DeleteModelsDialog.svelte';
	import RenameModelDialog from './RenameModelDialog.svelte';

	interface Props {
		org: string;
		models: ModelEntry[];
		now: number;
		/**
		 * Models checked, for members and admins (D130), whose rows also get a menu (D133). The
		 * page shows the selection bar, whose Delete opens this list's dialogs.
		 */
		selection?: Selection;
	}

	let { org, models, now, selection }: Props = $props();

	// The page's list, with models deleted or renamed here changed in place until it's read again.
	let rows = $derived(models);
	$effect.pre(() => {
		if (selection) selection.keys = rows.map((m) => m.name);
	});

	/** The model a dialog for one model acts on, as it was when opened. */
	let one = $state.raw<Pick<ModelEntry, 'name' | 'model' | 'versions'>>({
		name: '',
		model: '',
		versions: 0
	});
	$effect.pre(() => {
		const name = selection?.targets[0] ?? '';
		untrack(() => {
			one = rows.find((m) => m.name === name) ?? { name, model: `${org}/${name}`, versions: 0 };
		});
	});

	async function deleted(names: string[]) {
		const out = new Set(names);
		rows = rows.filter((m) => !out.has(m.name));
		selection?.drop(names);
		await invalidateAll();
	}

	async function renamed(from: string, to: string) {
		rows = rows.map((m) => (m.name === from ? { ...m, name: to, model: `${org}/${to}` } : m));
		selection?.drop([from]);
		await invalidateAll();
	}

	const saveSnippet = $derived(
		['from flock.hub import models', '', `models.save_to_hub(model, "${org}/my-model")`].join('\n')
	);
</script>

{#if !models.length}
	<div class="max-w-[68ch]">
		<p class="text-[15px]">No models yet.</p>
		<p class="mt-1 text-muted-foreground">Save a model from Python and it shows up here.</p>
		<div class="mt-3"><CopyBlock code={saveSnippet} /></div>
		<p class="mt-3 text-[13px] text-muted-foreground">
			Or push a folder with <code class="font-mono text-[0.9em]"
				>flock models push ./model {org}/my-model</code
			>, and load it back with
			<code class="font-mono text-[0.9em]">{pullSnippet(`${org}/my-model`)}</code>.
		</p>
	</div>
{:else}
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
			{#each rows as m (m.name)}
				{@const on = !!selection?.has(m.name)}
				<tr
					class={[
						'group/row h-10 border-b border-divider',
						on ? 'bg-selection/45' : 'hover:bg-muted'
					]}
					aria-selected={selection ? on : undefined}
				>
					{#if selection}
						<td class="pl-2"><RowCheckbox {selection} key={m.name} name={m.name} /></td>
					{/if}
					<td class="truncate px-2">
						<a
							class="flex items-center gap-2 underline-offset-3 hover:text-link hover:underline"
							href={modelsHref(org, m.name)}
						>
							<BoxIcon class="size-4 flex-none text-muted-foreground" />
							<span class="truncate">{m.name}</span>
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
								name={m.name}
								onrename={() => selection.act('rename', [m.name])}
								ondelete={() => selection.act('delete', [m.name])}
							/>
						</td>
					{/if}
				</tr>
			{/each}
		</tbody>
	</table>
{/if}

{#if selection}
	{@const many = selection.targets.length > 1}
	<DeleteModelsDialog
		bind:open={() => many && selection.action === 'delete', (o) => o || selection.close('delete')}
		{org}
		names={selection.targets}
		ondeleted={deleted}
	/>
	<DeleteModelDialog
		bind:open={() => !many && selection.action === 'delete', (o) => o || selection.close('delete')}
		{org}
		info={one}
		ondeleted={() => deleted([one.name])}
	/>
	<RenameModelDialog
		bind:open={() => selection.action === 'rename', (o) => o || selection.close('rename')}
		{org}
		name={one.name}
		onrenamed={(to) => renamed(one.name, to)}
	/>
{/if}
