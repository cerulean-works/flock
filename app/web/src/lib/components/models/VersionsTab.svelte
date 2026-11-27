<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import type { Scope } from '#lib/scope.ts';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import FootprintsIcon from '@lucide/svelte/icons/footprints';
	import StarIcon from '@lucide/svelte/icons/star';
	import TagIcon from '@lucide/svelte/icons/tag';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import type { ResolvedPathname } from '$app/types';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { DialogSubject } from '#lib/data/dialog-subject.svelte.ts';
	import { modelApi, modelCall, runPathHref, sortTags } from '#lib/models.ts';
	import type { ModelInfo, ModelVersion, ModelVersionsPage } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';
	import DataTable, { ROW_LINK, type Column } from '../DataTable.svelte';
	import { ROW_MENU_TRIGGER } from '../RowMenu.svelte';
	import TagChip from '../TagChip.svelte';
	import DeleteVersionDialog from './DeleteVersionDialog.svelte';
	import MakePrimaryDialog from './MakePrimaryDialog.svelte';
	import RemoveTagDialog from './RemoveTagDialog.svelte';
	import TagDialog from './TagDialog.svelte';
	import Skeleton from '#lib/components/Skeleton.svelte';

	interface Props {
		scope: Scope;
		info: ModelInfo;
		/** The version the page shows, null while there is none. */
		shown: number | null;
		/** The page's URL at another `?version=`. */
		versionHref: (version: string | null) => ResolvedPathname;
		/** Called after a version, a tag or the primary changed. */
		onchange?: () => unknown;
		now: number;
	}

	let { scope, info, shown, versionHref, onchange, now }: Props = $props();

	const columns: Column[] = [
		{ id: 'version', label: 'Version', size: 'fit' },
		{ id: 'tags', label: 'Tags' },
		{ id: 'run', label: 'Run', from: 'md' },
		{ id: 'size', label: 'Size', align: 'end', from: 'sm' },
		{ id: 'pushed', label: 'Pushed', align: 'end' }
	];
	const name = $derived(info.name);

	// Newest first, 100 a page.
	let versions = $state<ModelVersion[]>([]);
	let cursor = $state<number | null>(null);
	let loaded = $state(false);
	let loading = $state(false);
	let failed = $state<string | null>(null);

	async function load(more = false) {
		loading = true;
		failed = null;
		try {
			const page = await modelCall<ModelVersionsPage>(
				modelApi(scope, name, 'versions', { before: more ? cursor : null })
			);
			versions = more ? [...versions, ...page.versions] : page.versions;
			cursor = page.cursor;
			loaded = true;
		} catch (e) {
			failed = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void name;
		void load();
	});

	function changed() {
		void load();
		void onchange?.();
	}

	const tagging = new DialogSubject<number>();
	const removing = new DialogSubject<{ tag: string; version: number }>();
	// The primary is kept from when the dialog opened: the page reads it again after the change.
	const promoting = new DialogSubject<{ number: number; current: number | null }>();
	const deleting = new DialogSubject<ModelVersion>();
</script>

{#snippet cell(v: ModelVersion, id: string)}
	{#if id === 'version'}
		<span class="whitespace-nowrap">
			<a
				class={[ROW_LINK, 'tabular-nums', v.number === shown && 'font-medium']}
				href={versionHref(String(v.number))}
				aria-label="View version {v.number}">v{v.number}</a
			>
			{#if v.primary}<span class="ml-1.5 text-[12px] text-muted-foreground">Primary</span>{/if}
		</span>
	{:else if id === 'tags'}
		{#if v.tags.length}
			<ul class="relative z-1 flex flex-wrap gap-1" aria-label="Tags of version {v.number}">
				{#each sortTags(v.tags) as tag (tag)}
					<TagChip as="li" {tag} onremove={() => removing.show({ tag, version: v.number })} />
				{/each}
			</ul>
		{/if}
	{:else if id === 'run'}
		{@const runHref = v.run ? runPathHref(v.run) : null}
		<span class="flex items-center gap-2.5 whitespace-nowrap text-muted-foreground">
			{#if v.run}
				{#if runHref}
					<a class="relative z-1 text-link underline-offset-3 hover:underline" href={runHref}
						>{v.run.split('/').at(-1)}</a
					>
				{:else}
					{v.run}
				{/if}
			{/if}
			{#if v.step !== null}
				<span class="inline-flex items-center gap-1 tabular-nums">
					<FootprintsIcon class="size-3.5 text-faint" aria-hidden="true" />
					step {v.step.toLocaleString('en-US')}
				</span>
			{/if}
		</span>
	{:else if id === 'size'}
		<span class="text-muted-foreground">{bytes(v.total_bytes)}</span>
	{:else if id === 'pushed'}
		<span class="whitespace-nowrap text-muted-foreground" {@attach hint(dateTime(v.created_at))}
			>{v.created_by ? `${v.created_by.name}, ` : ''}{relativeTime(v.created_at, now)}</span
		>
	{/if}
{/snippet}

{#snippet menu(v: ModelVersion)}
	<DropdownMenu.Root>
		<DropdownMenu.Trigger class={ROW_MENU_TRIGGER} aria-label="Version {v.number} actions">
			<EllipsisIcon class="size-4" />
		</DropdownMenu.Trigger>
		<DropdownMenu.Content align="end" class="w-44">
			<DropdownMenu.Item>
				{#snippet child({ props })}
					<a {...props} href={versionHref(String(v.number))}>
						<EyeIcon /> View
					</a>
				{/snippet}
			</DropdownMenu.Item>
			<DropdownMenu.Item
				disabled={v.primary}
				onSelect={() => promoting.show({ number: v.number, current: info.primary })}
			>
				<StarIcon /> Make primary
			</DropdownMenu.Item>
			<DropdownMenu.Item onSelect={() => tagging.show(v.number)}>
				<TagIcon /> Add tag…
			</DropdownMenu.Item>
			<DropdownMenu.Separator />
			<DropdownMenu.Item variant="destructive" onSelect={() => deleting.show(v)}>
				<Trash2Icon /> Delete…
			</DropdownMenu.Item>
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/snippet}

{#snippet more()}
	{#if failed}<p class="text-[13px] text-destructive" role="alert">{failed}</p>{/if}
	{#if cursor !== null}
		<Button variant="quiet" size="bar" onclick={() => load(true)} pending={loading}
			>Load more</Button
		>
	{/if}
{/snippet}

{#if failed && !loaded}
	<p class="px-5 pt-5 text-[13px] text-destructive" role="alert">{failed}</p>
{:else if !loaded}
	<div class="px-5 pt-5"><Skeleton /></div>
{:else if !versions.length}
	<p class="px-5 pt-5 text-[13px] text-muted-foreground">No versions yet.</p>
{:else}
	<!-- The list tables' look (Gate 2): 36px rows to the edges, the row opens its version. -->
	<DataTable
		{columns}
		rows={versions}
		key={(v) => String(v.number)}
		name={(v) => `Version ${v.number}`}
		current={(v) => v.number === shown}
		{cell}
		{menu}
		footer={failed || cursor !== null ? more : undefined}
		label="Versions"
	/>
{/if}

<MakePrimaryDialog
	bind:open={promoting.open}
	{scope}
	{name}
	number={promoting.value?.number ?? 0}
	current={promoting.value?.current ?? null}
	onchanged={changed}
/>
<TagDialog
	bind:open={tagging.open}
	{scope}
	{name}
	version={tagging.value ?? 0}
	ontagged={changed}
/>
<RemoveTagDialog
	bind:open={removing.open}
	{scope}
	{name}
	tag={removing.value?.tag ?? ''}
	version={removing.value?.version ?? 0}
	onremoved={changed}
/>
<DeleteVersionDialog
	bind:open={deleting.open}
	{scope}
	version={deleting.value}
	shown={deleting.value?.number === shown}
	ondeleted={changed}
/>
