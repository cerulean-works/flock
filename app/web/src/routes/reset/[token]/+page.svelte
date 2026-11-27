<script lang="ts">
	import { enhance } from '$app/forms';
	import AuthShell from '#lib/components/AuthShell.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();

	let pending = $state(false);
</script>

<svelte:head>
	<title>{data.email ? 'Choose a new password' : 'Reset link'} · Flock</title>
</svelte:head>

<AuthShell title={data.email ? 'Choose a new password' : 'This link has expired'}>
	{#if !data.email}
		<Button variant="outline" class="mt-7 h-9 w-full" href="/login">Sign in</Button>
	{:else}
		<form
			method="POST"
			class="mt-7 grid gap-3"
			use:enhance={() => {
				pending = true;
				return async ({ update }) => {
					await update({ reset: false });
					pending = false;
				};
			}}
		>
			<!-- Also tells password managers which account the new password belongs to. -->
			<div class="grid gap-1.5">
				<Label for="username" class="text-[13px]">Email</Label>
				<Input
					id="username"
					name="username"
					autocomplete="username"
					value={data.email}
					class="h-9 bg-input/50 text-muted-foreground"
					readonly
				/>
			</div>
			<div class="grid gap-1.5">
				<Label for="password" class="text-[13px]">New password</Label>
				<Input
					id="password"
					name="password"
					type="password"
					autocomplete="new-password"
					minlength={8}
					class="h-9"
					required
				/>
			</div>
			<FormError error={form?.error ?? null} />
			<Button type="submit" class="mt-2 h-9 w-full" {pending}>Set password</Button>
		</form>
	{/if}
</AuthShell>
