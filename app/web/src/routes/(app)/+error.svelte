<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import Page from '#lib/components/Page.svelte';
	import { Button } from '#lib/components/ui/button/index.js';

	// Flock's messages start in lower case.
	const message = $derived.by(() => {
		const m = page.error?.message || 'Something went wrong';
		return m.charAt(0).toUpperCase() + m.slice(1);
	});
	const next = $derived(page.url.pathname + page.url.search);
</script>

<svelte:head>
	<title>{page.error?.noS3 ? 'Storage' : page.status} · Flock</title>
</svelte:head>

<!-- A page that failed, inside the shell so the way on stays at hand (E4). -->
<Page width="full">
	{#if page.error?.noS3}
		<EmptyState variant="onboarding" title="Storage isn’t set up on this server">
			{#snippet lead()}
				Datasets, Models and Storage keep their files in S3. Point Flock at a bucket store with
				<code class="font-mono text-[13px]">FLOCK_S3_UPSTREAM_ENDPOINT</code> to use them.
			{/snippet}
		</EmptyState>
	{:else}
		<div class="px-5 pt-[12vh] pb-16">
			<div class="mx-auto w-full max-w-[36rem] text-center">
				<h1 class="text-[20px] leading-[1.2] font-medium tracking-[-0.01em] text-balance">
					{message}
				</h1>
				<p class="mt-2 text-[14px] text-muted-foreground">
					{#if page.status === 502}
						Start Flock with <code class="font-mono text-[13px]">docker compose up -d --build</code>
						in <code class="font-mono text-[13px]">app</code>, or set
						<code class="font-mono text-[13px]">FLOCK_API_URL</code>.
					{:else if page.status === 404}
						Nothing is at this address, or it isn’t yours to see.
					{:else}
						Error {page.status}.
					{/if}
				</p>
				<div class="mt-5 flex justify-center">
					{#if page.status === 401}
						<Button
							variant="quiet"
							size="bar"
							href="/login?{new URLSearchParams({ next })}"
							data-sveltekit-reload>Sign in</Button
						>
					{:else}
						<Button variant="quiet" size="bar" href={resolve('/(app)')}>Go home</Button>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</Page>
