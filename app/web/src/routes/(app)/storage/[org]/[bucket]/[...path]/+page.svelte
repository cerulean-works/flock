<script lang="ts">
	import Breadcrumbs from '#lib/components/storage/Breadcrumbs.svelte';
	import ObjectBrowser from '#lib/components/storage/ObjectBrowser.svelte';
	import ObjectView from '#lib/components/storage/ObjectView.svelte';
	import { crumbs, nameOf } from '#lib/storage.ts';
	import type { PageProps } from './$types';

	let { data, params }: PageProps = $props();

	const title = $derived(data.key ? nameOf(data.key) : params.bucket);
</script>

<svelte:head>
	<title>{title} · Storage · {data.shell.orgName}</title>
</svelte:head>

<div class="flex min-h-0 flex-1 flex-col">
	<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
		<div class="mx-auto max-w-300">
			<div class="pt-5">
				<Breadcrumbs
					crumbs={crumbs(params.org, data.shell.orgName ?? params.org, params.bucket, data.key)}
				/>
			</div>
			<h1 class="truncate pt-3 pb-5 text-[34px] leading-[1.15] tracking-[-0.025em]" {title}>
				{title}{data.kind === 'folder' && data.key ? '/' : ''}
			</h1>
			{#if data.kind === 'folder'}
				<ObjectBrowser
					org={params.org}
					bucket={params.bucket}
					prefix={data.key}
					listing={data.listing}
					now={data.now}
				/>
			{:else}
				<ObjectView org={params.org} bucket={params.bucket} object={data.object} />
			{/if}
		</div>
	</main>
</div>
