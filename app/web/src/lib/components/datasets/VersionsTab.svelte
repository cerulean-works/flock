<script lang="ts">
	import EllipsisVerticalIcon from '@lucide/svelte/icons/ellipsis-vertical';
	import TagIcon from '@lucide/svelte/icons/tag';
	import XIcon from '@lucide/svelte/icons/x';
	import type { ResolvedPathname } from '$app/types';
	import { Button } from '#lib/components/ui/button/index.js';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import { datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { DatasetVersion, VersionsPage } from '#lib/types.ts';
	import { dateTime, relativeTime } from '#lib/utils.ts';
	import RemoveTagDialog from './RemoveTagDialog.svelte';
	import TagDialog from './TagDialog.svelte';

	interface Props {
		org: string;
		path: string;
		/** The version the page shows. */
		shown: number;
		/** The page's URL at another `?version=`. */
		versionHref: (version: string | null) => ResolvedPathname;
		/** Called after a tag was added, moved or removed. */
		ontags?: () => void;
		now: number;
	}

	let { org, path, shown, versionHref, ontags, now }: Props = $props();

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
				datasetsApi(org, 'versions', { path, cursor: more ? cursor : null })
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

	let tagging = $state<number | null>(null);
	let removing = $state<{ tag: string; version: number } | null>(null);

	/** The message a writer gave Lance's commit; it reads as the change's description. */
	const MESSAGE = '__lance_commit_message';
	const extra = (properties: Record<string, string>) =>
		Object.entries(properties).filter(([key]) => key !== MESSAGE);

	const rowsChange = (delta: number) =>
		`${delta > 0 ? '+' : '−'}${Math.abs(delta).toLocaleString('en-US')} ${Math.abs(delta) === 1 ? 'row' : 'rows'}`;
</script>

{#if failed && !loaded}
	<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{failed}</p>
{:else if !loaded}
	<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
{:else}
	<!-- Every cell starts on the same 24px line, so a row reads level whatever it holds. -->
	<table class="w-full border-collapse text-[13px] leading-6">
		<thead>
			<tr class="h-10 border-b text-left text-xs text-muted-foreground">
				<th class="w-px px-3 font-medium">Version</th>
				<th class="w-px px-3 font-medium">Tags</th>
				<th class="w-px px-3 font-medium">Written</th>
				<th class="px-3 font-medium">Change</th>
				<th class="w-px"><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		<tbody>
			{#each versions as v (v.version)}
				{@const message = v.properties[MESSAGE]}
				<tr
					class={['border-b border-divider align-top', v.version === shown && 'bg-muted']}
					aria-current={v.version === shown ? 'true' : undefined}
				>
					<td class="px-3 py-3 tabular-nums">{v.version}</td>
					<td class="px-3 py-3">
						{#if v.tags.length}
							<ul
								class="flex w-max max-w-72 flex-wrap gap-1"
								aria-label="Tags of version {v.version}"
							>
								{#each v.tags as tag (tag)}
									<li
										class="inline-flex h-6 max-w-full min-w-0 items-center gap-1 rounded-full bg-secondary pr-1 pl-2 text-xs"
									>
										<span class="truncate" title={tag}>{tag}</span>
										<button
											class="grid size-4 flex-none place-items-center rounded-full text-muted-foreground hover:bg-background hover:text-foreground"
											aria-label="Remove the tag {tag}"
											onclick={() => (removing = { tag, version: v.version })}
										>
											<XIcon class="size-3" />
										</button>
									</li>
								{/each}
							</ul>
						{:else}
							<span class="text-faint">None</span>
						{/if}
					</td>
					<td
						class="px-3 py-3 whitespace-nowrap text-muted-foreground tabular-nums"
						title={dateTime(v.timestamp)}
					>
						{relativeTime(v.timestamp, now)}
					</td>
					<td class="px-3 py-3">
						<p>
							{v.operation ?? 'Unknown'}
							{#if v.rows_delta}
								<span class="ml-1 text-muted-foreground tabular-nums"
									>{rowsChange(v.rows_delta)}</span
								>
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
					</td>
					<td class="py-2.5 pr-2 pl-3 whitespace-nowrap">
						<div class="flex items-center gap-1">
							<Button
								variant="ghost"
								size="sm"
								class="text-muted-foreground"
								href={versionHref(String(v.version))}
								aria-label="View version {v.version}"
							>
								View
							</Button>
							<DropdownMenu.Root>
								<DropdownMenu.Trigger>
									{#snippet child({ props })}
										<Button
											{...props}
											variant="ghost"
											size="icon-sm"
											class="text-muted-foreground"
											aria-label="Version {v.version} actions"
										>
											<EllipsisVerticalIcon />
										</Button>
									{/snippet}
								</DropdownMenu.Trigger>
								<DropdownMenu.Content align="end" class="w-40">
									<DropdownMenu.Item onSelect={() => (tagging = v.version)}>
										<TagIcon /> Add tag…
									</DropdownMenu.Item>
								</DropdownMenu.Content>
							</DropdownMenu.Root>
						</div>
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
	{#if failed}
		<p class="mt-2 text-[13px] text-destructive" role="alert">{failed}</p>
	{/if}
	{#if cursor !== null}
		<div class="mt-3">
			<Button variant="outline" size="sm" disabled={loading} onclick={() => load(true)}>
				{loading ? 'Loading…' : 'Load more'}
			</Button>
		</div>
	{/if}
{/if}

<TagDialog
	bind:open={() => tagging !== null, (o) => (tagging = o ? tagging : null)}
	{org}
	{path}
	version={tagging ?? 0}
	ontagged={changed}
/>
<RemoveTagDialog
	bind:open={() => removing !== null, (o) => (removing = o ? removing : null)}
	{org}
	{path}
	tag={removing?.tag ?? ''}
	version={removing?.version ?? 0}
	onremoved={changed}
/>
