<script lang="ts">
	import { enhance } from '$app/forms';
	import AuthShell from '#lib/components/AuthShell.svelte';
	import GoogleButton from '#lib/components/GoogleButton.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();

	let pending = $state(false);
</script>

<svelte:head>
	<title>Set up Flock</title>
</svelte:head>

<AuthShell>
	<h1 class="font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">Set up Flock</h1>
	<p class="mt-3 leading-relaxed text-muted-foreground">
		The first account becomes the super admin. Create it before sharing this address.
	</p>

	{#if data.google}
		<div class="mt-8">
			<GoogleButton label="Set up with Google" />
		</div>

		<div class="my-6 flex items-center gap-3 text-xs text-muted-foreground">
			<span class="h-px flex-1 bg-border"></span>
			or with a password
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
			<Label for="name">Name</Label>
			<Input
				id="name"
				name="name"
				autocomplete="name"
				value={form?.name ?? ''}
				class="h-10"
				required
			/>
		</div>
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
				autocomplete="new-password"
				minlength={8}
				class="h-10"
				required
			/>
			<p class="text-xs text-muted-foreground">At least 8 characters.</p>
		</div>
		{#if form?.error}
			<p class="text-[13px] text-destructive" role="alert">
				{form.error.charAt(0).toUpperCase() + form.error.slice(1)}.
			</p>
		{/if}
		<Button type="submit" class="mt-2 h-10 w-full text-sm" disabled={pending}>
			{pending ? 'Creating account…' : 'Create account'}
		</Button>
	</form>
</AuthShell>
