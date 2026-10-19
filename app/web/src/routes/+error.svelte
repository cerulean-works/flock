<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { Button } from '#lib/components/ui/button/index.js';

	// Flock's messages start in lower case.
	const message = $derived.by(() => {
		const m = page.error?.message || 'Something went wrong';
		return m.charAt(0).toUpperCase() + m.slice(1);
	});
	const next = $derived(page.url.pathname + page.url.search);
</script>

<svelte:head>
	<title>{page.status} · Flock</title>
</svelte:head>

<main class="mx-auto mt-[12vh] max-w-160 px-6">
	<p class="text-[13px] text-muted-foreground">{page.status}</p>
	<h1 class="mt-1 mb-4 font-serif text-4xl leading-tight tracking-[-0.02em]">
		{message}
	</h1>
	{#if page.status === 502}
		<p class="mb-6 font-serif text-[19px] leading-normal">
			Start Flock with
			<code class="rounded-md bg-muted px-1.5 py-0.5 font-mono text-sm"
				>docker compose up -d --build</code
			>
			in <code class="rounded-md bg-muted px-1.5 py-0.5 font-mono text-sm">app</code>, or
			set <code class="rounded-md bg-muted px-1.5 py-0.5 font-mono text-sm">FLOCK_URL</code>.
		</p>
	{/if}
	{#if page.status === 401}
		<Button href="/login?{new URLSearchParams({ next })}" data-sveltekit-reload>Sign in</Button>
	{:else}
		<Button variant="outline" href={resolve('/(app)')}>Back to home</Button>
	{/if}
</main>
