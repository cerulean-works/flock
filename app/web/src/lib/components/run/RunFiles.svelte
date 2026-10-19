<script lang="ts">
	import FileIcon from '@lucide/svelte/icons/file';
	import { onMount } from 'svelte';
	import type { ResolvedPathname } from '$app/types';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import type { RunFile } from '#lib/types.ts';
	import { bytes, dateTime, relativeTime } from '#lib/utils.ts';

	interface Props {
		/** `/api/projects/…/runs/{run}/files` */
		url: string;
		now: number;
	}

	let { url, now }: Props = $props();

	let files = $state.raw<RunFile[] | null>(null);
	let failed = $state(false);

	onMount(() => {
		fetch(url)
			.then((res) => (res.ok ? res.json() : Promise.reject(new Error(res.statusText))))
			.then((body: { files: RunFile[] }) => {
				files = body.files.toSorted((a, b) => a.name.localeCompare(b.name));
			})
			.catch(() => (failed = true));
	});

	// A file of the dashboard's API, not a page.
	const fileUrl = (name: string) =>
		`${url}/${name.split('/').map(encodeURIComponent).join('/')}` as ResolvedPathname;
</script>

<!-- On the page's left gutter, as the other tabs are; kept to a width a list of files reads at. -->
<div class="absolute inset-0 overflow-y-auto">
	{#if failed}
		<EmptyState tone="error">Couldn’t load the run’s files</EmptyState>
	{:else if !files}
		<EmptyState>Loading…</EmptyState>
	{:else if !files.length}
		<EmptyState>No files uploaded</EmptyState>
	{:else}
		<div class="px-5 pt-6 pb-16">
			<div class="max-w-200 overflow-hidden rounded-xl ring-1 ring-foreground/10">
				<table class="w-full text-[13.5px]">
					<thead class="border-b text-xs text-muted-foreground">
						<tr>
							<th class="px-3 py-2 text-left font-medium">Name</th>
							<th class="w-28 px-3 py-2 text-right font-medium">Size</th>
							<th class="w-36 px-3 py-2 text-right font-medium">Updated</th>
						</tr>
					</thead>
					<tbody>
						{#each files as f (f.name)}
							<tr class="relative border-b border-divider last:border-0 hover:bg-muted">
								<td class="px-3 py-2">
									<!-- The link covers the row. Plain text and images open; anything else downloads. -->
									<a
										class="flex min-w-0 items-center gap-2 font-mono text-[12.5px] after:absolute after:inset-0 hover:text-link"
										href={fileUrl(f.name)}
										target="_blank"
										rel="noopener"
										data-sveltekit-reload
									>
										<FileIcon class="size-4 flex-none text-muted-foreground" />
										<span class="truncate">{f.name}</span>
									</a>
								</td>
								<td class="px-3 py-2 text-right text-muted-foreground tabular-nums"
									>{bytes(f.size)}</td
								>
								<td
									class="px-3 py-2 text-right text-muted-foreground"
									title={dateTime(f.updated_at)}
								>
									{relativeTime(f.updated_at, now)}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	{/if}
</div>
