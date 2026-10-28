<script lang="ts">
	import { DatasetError, datasetCall, datasetsApi } from '#lib/datasets.ts';
	import type { DatasetInfo } from '#lib/types.ts';
	import { bytes, dateTime } from '#lib/utils.ts';
	import Markdown from '../Markdown.svelte';

	interface Props {
		org: string;
		path: string;
		/** The version shown; null when Lance couldn't read it, and only the README shows. */
		info: DatasetInfo | null;
		/** The tags on that version. */
		tags: string[];
	}

	let { org, path, info, tags }: Props = $props();

	let readme = $state<{ text: string; truncated: boolean } | null>(null);
	let missing = $state(false);
	let failed = $state<string | null>(null);

	// README.md in the dataset's folder (D92). It isn't versioned: every version shows this one.
	$effect(() => {
		const controller = new AbortController();
		readme = null;
		missing = false;
		failed = null;
		datasetCall<{ text: string; truncated: boolean }>(datasetsApi(org, 'readme', { path }), {
			signal: controller.signal
		})
			.then((got) => (readme = got))
			.catch((e: Error) => {
				if (controller.signal.aborted) return;
				if (e instanceof DatasetError && e.status === 404) missing = true;
				else failed = e.message;
			});
		return () => controller.abort();
	});

	const facts = $derived(
		info
			? [
					['Rows', info.rows.toLocaleString('en-US')],
					['Columns', info.columns.toLocaleString('en-US')],
					['Fragments', info.fragments.toLocaleString('en-US')],
					['Data size', bytes(info.data_size)],
					['Written', dateTime(info.timestamp)]
				]
			: []
	);
</script>

<!-- The README beside this version's numbers. -->
<div class="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
	<div class="min-w-0">
		{#if missing}
			<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">
				This dataset has no README. Add a README.md to its folder to describe it.
			</p>
		{:else if failed}
			<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{failed}</p>
		{:else if readme}
			<Markdown text={readme.text} />
			{#if readme.truncated}
				<p class="mt-4 text-[13px] text-muted-foreground">Showing the first 1 MB of README.md.</p>
			{/if}
		{:else}
			<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
		{/if}
	</div>

	{#if info}
		<aside aria-labelledby="about-version">
			<h2 id="about-version" class="text-[13px] text-muted-foreground">
				Version {info.version}
			</h2>
			<table class="mt-1 w-full border-collapse text-[13px]">
				<tbody>
					{#each facts as [label, value] (label)}
						<tr class="border-b border-divider">
							<th scope="row" class="py-2 pr-4 text-left font-normal text-muted-foreground">
								{label}
							</th>
							<td class="py-2 text-right tabular-nums">{value}</td>
						</tr>
					{/each}
					{#if tags.length}
						<tr class="border-b border-divider">
							<th
								scope="row"
								class="py-2 pr-4 text-left align-top font-normal text-muted-foreground"
							>
								{tags.length === 1 ? 'Tag' : 'Tags'}
							</th>
							<td class="py-2 text-right break-all">{tags.join(', ')}</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</aside>
	{/if}
</div>
