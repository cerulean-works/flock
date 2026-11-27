<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import EmptyState from '#lib/components/EmptyState.svelte';
	import Page from '#lib/components/Page.svelte';
	import PageHeader from '#lib/components/PageHeader.svelte';
	import { TABS, missingHeading, missingTrail } from '#lib/not-found.ts';
	import { headerTrail } from '#lib/storage.ts';
	import { Button } from '#lib/components/ui/button/index.js';

	// A 404 says what kind of thing isn't there, one way ("No folder at this address").
	const message = $derived.by(() => {
		if (page.status === 404) return missingHeading(page.error?.message);
		const m = page.error?.message || 'Something went wrong';
		return m.charAt(0).toUpperCase() + m.slice(1);
	});
	// The header: the trail up to the last part of the address that exists, which is also the
	// way on beside Home. The org leads it where the page's own header would (not on Datasets,
	// Models and Storage) and only when it's real; the project too.
	const shell = $derived(page.data.shell);
	const first = $derived(page.url.pathname.split('/').filter(Boolean)[0] ?? '');
	const realOrg = $derived(!!shell?.org && (page.params.entity ?? page.params.org) === shell.org);
	const withOrg = $derived(realOrg && !(first in TABS) && first !== 'account' && first !== 'admin');
	const realProject = $derived(
		page.params.project &&
			shell?.projects?.some((p: { name: string }) => p.name === page.params.project)
			? page.params.project
			: null
	);
	const trail = $derived(missingTrail(page.url.pathname) ?? []);
	const header = $derived(
		headerTrail(trail, realProject ?? (withOrg ? (shell?.orgName ?? 'Not found') : 'Not found'))
	);
	const parent = $derived(trail.at(-1) ?? null);
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
		<PageHeader
			title={header.title}
			trail={header.trail}
			org={withOrg}
			project={realProject && realProject !== header.title ? realProject : null}
		/>
		<div class="px-5 pt-[12vh] pb-16">
			<div class="mx-auto w-full max-w-[36rem] text-center">
				<!-- The header's title is the page's heading. -->
				<h2 class="text-[20px] leading-[1.2] font-medium tracking-[-0.01em] text-balance">
					{message}
				</h2>
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
				<div class="mt-5 flex justify-center gap-2">
					{#if page.status === 401}
						<Button
							variant="outline"
							size="sm"
							href="/login?{new URLSearchParams({ next })}"
							data-sveltekit-reload>Sign in</Button
						>
					{:else}
						{#if parent}
							<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- this address's parent -->
							<Button variant="outline" size="sm" href={parent.href}>Open {parent.label}</Button>
						{/if}
						<Button variant="outline" size="sm" href={resolve('/(app)')}>Go home</Button>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</Page>
