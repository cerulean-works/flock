<script lang="ts">
	import TagIcon from '@lucide/svelte/icons/tag';
	import XIcon from '@lucide/svelte/icons/x';
	import type { ResolvedPathname } from '$app/types';
	import { Button } from '#lib/components/ui/button/index.js';
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

	const rowsChange = (delta: number) =>
		`${delta > 0 ? '+' : '−'}${Math.abs(delta).toLocaleString('en-US')} ${Math.abs(delta) === 1 ? 'row' : 'rows'}`;
</script>

{#if failed && !loaded}
	<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{failed}</p>
{:else if !loaded}
	<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
{:else}
	<table class="w-full border-collapse text-[13px]">
		<thead>
			<tr class="h-9 border-b text-left text-xs text-muted-foreground">
				<th class="w-px px-2 font-medium">Version</th>
				<th class="w-px px-2 font-medium">Written</th>
				<th class="px-2 font-medium">Change</th>
				<th class="w-px"><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		<tbody>
			{#each versions as v (v.version)}
				<tr
					class={['border-b border-divider align-top', v.version === shown && 'bg-muted']}
					aria-current={v.version === shown ? 'true' : undefined}
				>
					<td class="px-2 py-2">
						<!-- Tags sit beside the number, so a tagged row keeps the height of the others. -->
						<div class="flex min-h-7 items-center gap-2 whitespace-nowrap">
							<span class="tabular-nums">{v.version}</span>
							{#if v.tags.length}
								<ul class="contents" aria-label="Tags of version {v.version}">
									{#each v.tags as tag (tag)}
										<li
											class="inline-flex h-6 max-w-56 min-w-0 items-center gap-1 rounded-full bg-secondary pr-1 pl-2 text-xs"
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
							{/if}
						</div>
					</td>
					<td
						class="px-2 py-2 leading-7 whitespace-nowrap text-muted-foreground tabular-nums"
						title={dateTime(v.timestamp)}
					>
						{relativeTime(v.timestamp, now)}
					</td>
					<td class="px-2 py-2">
						<span class="leading-7">{v.operation ?? 'Unknown'}</span>
						{#if v.rows_delta}
							<span class="ml-1 text-muted-foreground tabular-nums">{rowsChange(v.rows_delta)}</span
							>
						{/if}
						{#each Object.entries(v.properties) as [key, value] (key)}
							<p class="mt-0.5 text-[12.5px]">
								<span class="text-muted-foreground">{key}</span>
								{value}
							</p>
						{/each}
					</td>
					<td class="px-2 py-2 text-right whitespace-nowrap">
						<Button
							variant="ghost"
							size="sm"
							class="text-muted-foreground"
							href={versionHref(String(v.version))}
							aria-label="View version {v.version}"
						>
							View
						</Button>
						<Button
							variant="ghost"
							size="sm"
							class="text-muted-foreground"
							aria-label="Add a tag to version {v.version}"
							onclick={() => (tagging = v.version)}
						>
							<TagIcon /> Add tag
						</Button>
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
