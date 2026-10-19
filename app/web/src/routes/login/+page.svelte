<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import AuthShell from '#lib/components/AuthShell.svelte';
	import GoogleButton from '#lib/components/GoogleButton.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();

	// A failed Google sign-in comes back here with `error` (a reason) and `message` (for people).
	const googleError = $derived(
		page.url.searchParams.get('message') ?? page.url.searchParams.get('error')
	);
	let pending = $state(false);
</script>

<svelte:head>
	<title>Sign in · Flock</title>
</svelte:head>

<AuthShell>
	<h1 class="font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">Sign in to Flock</h1>
	<p class="mt-3 leading-relaxed text-muted-foreground">Experiment tracking for Cerulean.</p>

	{#if googleError}
		<p
			class="mt-6 rounded-lg bg-destructive/10 px-3 py-2.5 text-[13px] text-destructive"
			role="alert"
		>
			Google sign-in failed: {googleError}.
		</p>
	{/if}

	{#if data.google}
		<div class="mt-8">
			<GoogleButton />
		</div>

		<div class="my-6 flex items-center gap-3 text-xs text-muted-foreground">
			<span class="h-px flex-1 bg-border"></span>
			or with email
			<span class="h-px flex-1 bg-border"></span>
		</div>
	{/if}

	<form
		method="POST"
		class={['grid gap-4', !data.google && 'mt-8']}
		use:enhance={() => {
			pending = true;
			return async ({ update }) => {
				await update({ reset: false });
				pending = false;
			};
		}}
	>
		<div class="grid gap-2">
			<Label for="email">Email</Label>
			<Input
				id="email"
				name="email"
				type="email"
				autocomplete="email"
				value={form?.email ?? ''}
				class="h-10"
				required
			/>
		</div>
		<div class="grid gap-2">
			<Label for="password">Password</Label>
			<Input
				id="password"
				name="password"
				type="password"
				autocomplete="current-password"
				class="h-10"
				required
			/>
		</div>
		{#if form?.error}
			<p class="text-[13px] text-destructive" role="alert">
				{form.error.charAt(0).toUpperCase() + form.error.slice(1)}.
			</p>
		{/if}
		<Button type="submit" class="mt-2 h-10 w-full text-sm" disabled={pending}>
			{pending ? 'Signing in…' : 'Sign in'}
		</Button>
	</form>
</AuthShell>
