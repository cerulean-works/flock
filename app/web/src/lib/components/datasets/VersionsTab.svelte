<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import type { Scope } from '#lib/scope.ts';
	import TagIcon from '@lucide/svelte/icons/tag';
	import type { ResolvedPathname } from '$app/types';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { DialogSubject } from '#lib/data/dialog-subject.svelte.ts';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { DatasetVersion, VersionsPage } from '#lib/types.ts';
	import { dateTime, relativeTime } from '#lib/utils.ts';
	import DataTable, { ROW_LINK, type Column } from '../DataTable.svelte';
	import TagChip from '../TagChip.svelte';
	import RemoveTagDialog from './RemoveTagDialog.svelte';
	import TagDialog from './TagDialog.svelte';
	import { openMenuTrigger } from '../AnchoredForm.svelte';
	import Skeleton from '#lib/components/Skeleton.svelte';

	interface Props {
		scope: Scope;
		path: string;
		/** The version the page shows. */
		shown: number;
		/** The page's URL at another `?version=`. */
		versionHref: (version: string | null) => ResolvedPathname;
		/** Called after a tag was added, moved or removed. */
		ontags?: () => void;
		/** Outside the org: tags can't be changed (D115). */
		readonly?: boolean;
		now: number;
	}

	let { scope, path, shown, versionHref, ontags, readonly = false, now }: Props = $props();

	const columns: Column[] = [
		{ id: 'version', label: 'Version', size: 'fit' },
		{ id: 'change', label: 'Change' },
		{ id: 'tags', label: 'Tags', from: 'sm' },
		{ id: 'written', label: 'Written', align: 'end' }
	];

	// The history, newest first, 50 a page (D89).
	let versions = $state<DatasetVersion[]>([]);
	let cursor = $state<number | null>(null);
	let loaded = $state(false);
	let loading = $state(false);
	let failed = $state<string | null>(null);

	async function load(more = false) {
		loading = true;
		failed = null;
		try {
			const page = await datasetCall<VersionsPage>(
				datasetsApi(scope, 'versions', { path, cursor: more ? cursor : null })
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
		void path;
		void load();
	});

	function changed() {
		void load();
		ontags?.();
	}

	const tagging = new DialogSubject<number>();
	let tagAnchor = $state<HTMLElement | null>(null);
	const removing = new DialogSubject<{ tag: string; version: number }>();

	/** The message a writer gave Lance's commit; it reads as the change's description. */
	const MESSAGE = '__lance_commit_message';
	const extra = (properties: Record<string, string>) =>
		Object.entries(properties).filter(([key]) => key !== MESSAGE);

	const rowsChange = (delta: number) =>
		`${delta > 0 ? '+' : '−'}${Math.abs(delta).toLocaleString('en-US')} ${Math.abs(delta) === 1 ? 'row' : 'rows'}`;
</script>

{#snippet cell(v: DatasetVersion, id: string)}
	{#if id === 'version'}
		<a
			class={[ROW_LINK, 'tabular-nums', v.version === shown && 'font-medium']}
			href={versionHref(String(v.version))}
			aria-label="View version {v.version}">v{v.version}</a
		>
	{:else if id === 'change'}
		{@const message = v.properties[MESSAGE]}
		<p>
			{v.operation ?? 'Unknown'}
			{#if v.rows_delta}
				<span class="ml-1 text-muted-foreground tabular-nums">{rowsChange(v.rows_delta)}</span>
			{/if}
		</p>
		{#if message}
			<p class="whitespace-pre-line text-muted-foreground">{message}</p>
		{/if}
		{#each extra(v.properties) as [key, value] (key)}
			<p class="text-[12px] leading-5">
				<span class="font-mono text-faint">{key}</span>
				<span class="text-muted-foreground">{value}</span>
			</p>
		{/each}
	{:else if id === 'tags'}
		{#if v.tags.length || v.kept_for}
			<div class="relative z-1 flex flex-wrap items-center gap-x-2 gap-y-1">
				{#if v.tags.length}
					<ul class="flex flex-wrap gap-1" aria-label="Tags of version {v.version}">
						{#each v.tags as tag (tag)}
							<TagChip
								as="li"
								{tag}
								onremove={readonly ? undefined : () => removing.show({ tag, version: v.version })}
							/>
						{/each}
					</ul>
				{/if}
				{#if v.kept_for}
					<span
						class="text-xs whitespace-nowrap text-muted-foreground"
						{@attach hint("Forks or blends read this version, so it's never cleaned up (D173).")}
					>
						kept for {v.kept_for} dataset{v.kept_for === 1 ? '' : 's'}
					</span>
				{/if}
			</div>
		{/if}
	{:else if id === 'written'}
		<span class="text-muted-foreground" {@attach hint(dateTime(v.timestamp))}
			>{relativeTime(v.timestamp, now)}</span
		>
	{/if}
{/snippet}

{#snippet menu(v: DatasetVersion)}
	<DropdownMenu.Item
		onSelect={() => {
			tagAnchor = openMenuTrigger();
			tagging.show(v.version);
		}}
	>
		<TagIcon /> Add tag…
	</DropdownMenu.Item>
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
{:else}
	<!-- The list tables' look (Gate 2): 36px rows to the edges, the row opens its version. -->
	<DataTable
		{columns}
		rows={versions}
		key={(v) => String(v.version)}
		name={(v) => `Version ${v.version}`}
		current={(v) => v.version === shown}
		{cell}
		menu={readonly ? undefined : menu}
		footer={failed || cursor !== null ? more : undefined}
		label="Versions"
	/>
{/if}

{#if !readonly}
	<TagDialog
		bind:open={tagging.open}
		anchor={tagAnchor}
		{scope}
		{path}
		version={tagging.value ?? 0}
		ontagged={changed}
	/>
	<RemoveTagDialog
		bind:open={removing.open}
		{scope}
		{path}
		tag={removing.value?.tag ?? ''}
		version={removing.value?.version ?? 0}
		onremoved={changed}
	/>
{/if}
