<script lang="ts">
	import { DatasetError, datasetCall, datasetsApi } from '#lib/datasets.ts';
	import Markdown from '../Markdown.svelte';

	let { org, path }: { org: string; path: string } = $props();

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
</script>

{#if missing}
	<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">
		This dataset has no README. Add a README.md to its folder to describe it.
	</p>
{:else if failed}
	<p class="bg-card px-4 py-6 text-[13px] text-destructive" role="alert">{failed}</p>
{:else if readme}
	<Markdown text={readme.text} class="bg-card px-5 py-4" />
	{#if readme.truncated}
		<p class="mt-2 text-[13px] text-muted-foreground">Showing the first 1 MB of README.md.</p>
	{/if}
{:else}
	<p class="bg-card px-4 py-6 text-[13px] text-muted-foreground">Loading…</p>
{/if}
