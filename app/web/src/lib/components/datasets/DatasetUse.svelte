<script lang="ts">
	import { resolve } from '$app/paths';
	import CopyBlock from '#lib/components/CopyBlock.svelte';
	import { awsSnippet, lanceSnippet, loadSnippet } from '#lib/datasets.ts';
	import type { DatasetInfo } from '#lib/types.ts';

	interface Props {
		dataset: DatasetInfo;
		/** The version to pin snippets to; the latest when null. */
		version: number | null;
		/** Flock's API, as `flock.hub` reaches it. */
		flockUrl: string;
		/** The hub's S3 endpoint and region; no endpoint when the server hasn't set one. */
		s3: { endpoint: string | null; region: string };
	}

	let { dataset, version, flockUrl, s3 }: Props = $props();

	const account = resolve('/(app)/account');
</script>

<div class="mx-auto max-w-180 px-5 pt-8 pb-16">
	<section>
		<h2 class="font-serif text-2xl tracking-[-0.01em]">From Python</h2>
		<p class="mt-2 max-w-[62ch] text-muted-foreground">
			<code class="font-mono text-[13px]">flock.hub</code> loads a version as a Hugging Face dataset{#if version},
				pinned to version {version}{/if}. It signs in with an
			<a class="text-link underline underline-offset-3" href={account}>API key</a>.
		</p>
		<div class="mt-4 grid gap-3">
			<CopyBlock code={`export FLOCK_API_URL=${flockUrl}\nexport FLOCK_API_KEY=<your API key>`} />
			<CopyBlock code={loadSnippet(dataset.org, dataset.name, version)} />
		</div>
	</section>

	<section class="mt-10">
		<h2 class="font-serif text-2xl tracking-[-0.01em]">Over S3</h2>
		{#if s3.endpoint}
			<p class="mt-2 max-w-[62ch] text-muted-foreground">
				Lance, DuckDB and the AWS CLI read it at
				<code class="font-mono text-[13px]">{dataset.uri}</code> with an
				<a class="text-link underline underline-offset-3" href="{account}#s3-keys">S3 key</a>.
			</p>
			<div class="mt-4 grid gap-3">
				<CopyBlock code={lanceSnippet(dataset.uri, s3.endpoint, s3.region, version)} />
				<CopyBlock code={awsSnippet(dataset.uri, s3.endpoint)} />
			</div>
		{:else}
			<p class="mt-2 max-w-[62ch] text-muted-foreground">
				This server doesn’t name its S3 endpoint (<code class="font-mono text-[13px]"
					>FLOCK_HUB_PROXY_PUBLIC_URL</code
				>), so S3 tools have nowhere to connect yet.
			</p>
		{/if}
	</section>
</div>
