<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { missingTrail } from '#lib/not-found.ts';
	import { headerTrail } from '#lib/storage.ts';
	import { Button } from '#lib/components/ui/button/index.js';

	// Flock's messages start in lower case. A 404's "folder -/a/b not found" names what's missing
	// by its raw path: say what kind of thing isn't there instead.
	const message = $derived.by(() => {
		const m = page.error?.message || 'Something went wrong';
		const missing = page.status === 404 && /^(\w+) .+ not found$/i.exec(m);
		if (missing) return `No ${missing[1].toLowerCase()} at this address`;
		return m.charAt(0).toUpperCase() + m.slice(1);
	});
	// Where the missing address sits: the header's trail up to the last part above it, which is
	// also offered as the way on, beside Home.
	const trail = $derived(page.status === 404 ? missingTrail(page.url.pathname) : null);
	const parent = $derived(trail?.at(-1) ?? null);
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
		{#if trail}
			{@const header = headerTrail(trail, '')}
			<PageHeader title={header.title} trail={header.trail} />
		{/if}
		<div class="px-5 pt-[12vh] pb-16">
			<div class="mx-auto w-full max-w-[36rem] text-center">
				<!-- The header's title is the page's heading when it has one. -->
				<svelte:element
					this={trail ? 'h2' : 'h1'}
					class="text-[20px] leading-[1.2] font-medium tracking-[-0.01em] text-balance"
				>
					{message}
				</svelte:element>
				<p class="mt-2 text-[14px] text-muted-foreground">
					{#if page.status === 502}
						Start Flock with <code class="font-mono text-[13px]">docker compose up -d --build</code>
						in <code class="font-mono text-[13px]">app</code>, or set
						<code class="font-mono text-[13px]">FLOCK_API_URL</code>.
					{:else if page.status === 404}
						It may have moved, or it isn’t yours to see.
					{:else}
						Error {page.status}.
					{/if}
				</p>
				<div class="mt-5 flex justify-center gap-1">
					{#if page.status === 401}
						<Button
							variant="quiet"
							size="bar"
							href="/login?{new URLSearchParams({ next })}"
							data-sveltekit-reload>Sign in</Button
						>
					{:else}
						{#if parent}
							<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- this address's parent -->
							<Button variant="quiet" size="bar" href={parent.href}>Open {parent.label}</Button>
						{/if}
						<Button variant="quiet" size="bar" href={resolve('/(app)')}>Go home</Button>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</Page>
