<script lang="ts">
	import type { Scope } from '#lib/scope.ts';
	import { DatasetError, datasetCall, datasetsApi } from '#lib/datasets.ts';
	import Markdown from '../Markdown.svelte';
	import Skeleton from '#lib/components/Skeleton.svelte';

	interface Props {
		scope: Scope;
		path: string;
	}

	let { scope, path }: Props = $props();

	let readme = $state<{ text: string; truncated: boolean } | null>(null);
	let missing = $state(false);
	let failed = $state<string | null>(null);

	// README.md in the dataset's folder (D92). It isn't versioned: every version shows this one.
	$effect(() => {
		const controller = new AbortController();
		readme = null;
		missing = false;
		failed = null;
		datasetCall<{ text: string; truncated: boolean }>(datasetsApi(scope, 'readme', { path }), {
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

<!-- The dataset's README (its facts are in the page's properties pane). -->
<div class="max-w-3xl">
	{#if missing}
		<p class="text-[13px] text-muted-foreground">
			No README. Add a README.md to its folder to describe it.
		</p>
	{:else if failed}
		<p class="text-[13px] text-destructive" role="alert">{failed}</p>
	{:else if readme}
		<Markdown text={readme.text} />
		{#if readme.truncated}
			<p class="mt-4 text-[13px] text-muted-foreground">Showing the first 1 MB of README.md.</p>
		{/if}
	{:else}
		<Skeleton shape="lines" count={6} />
	{/if}
</div>
