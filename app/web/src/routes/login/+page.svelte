<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import AuthDivider from '#lib/components/AuthDivider.svelte';
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

<AuthShell title="Sign in to Flock">
	{#if googleError}
		<p
			class="mt-6 rounded-lg bg-destructive/10 px-3 py-2 text-[13px] text-destructive"
			role="alert"
		>
			Google sign-in failed: {googleError}.
		</p>
	{/if}

	{#if data.google}
		<div class="mt-7">
			<GoogleButton />
		</div>
		<AuthDivider />
	{/if}

	<form
		method="POST"
		class={['grid gap-3', !data.google && 'mt-7']}
		use:enhance={() => {
			pending = true;
			return async ({ update }) => {
				await update({ reset: false });
				pending = false;
			};
		}}
	>
		<div class="grid gap-1.5">
			<Label for="email" class="text-[13px]">Email</Label>
			<Input
				id="email"
				name="email"
				type="email"
				autocomplete="email"
				value={form?.email ?? ''}
				class="h-9"
				required
			/>
		</div>
		<div class="grid gap-1.5">
			<Label for="password" class="text-[13px]">Password</Label>
			<Input
				id="password"
				name="password"
				type="password"
				autocomplete="current-password"
				class="h-9"
				required
			/>
		</div>
		{#if form?.error}
			<p class="text-[13px] text-destructive" role="alert">
				{form.error.charAt(0).toUpperCase() + form.error.slice(1)}.
			</p>
		{/if}
		<Button type="submit" class="mt-2 h-9 w-full" {pending}>Sign in</Button>
	</form>
</AuthShell>
