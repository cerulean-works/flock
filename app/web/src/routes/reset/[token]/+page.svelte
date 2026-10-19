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

<AuthShell>
	{#if !data.email}
		<h1 class="font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">
			This link can’t be used
		</h1>
		<p class="mt-3 leading-relaxed text-muted-foreground">It was used or has expired.</p>
		<Button variant="outline" class="mt-8 h-10 w-full text-sm" href="/login">Sign in</Button>
	{:else}
		<h1 class="font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">Choose a new password</h1>
		<p class="mt-3 leading-relaxed text-muted-foreground">
			For <span class="text-foreground">{data.email}</span>. Other sessions are signed out.
		</p>
		<form
			method="POST"
			class="mt-8 grid gap-4"
			use:enhance={() => {
				pending = true;
				return async ({ update }) => {
					await update({ reset: false });
					pending = false;
				};
			}}
		>
			<!-- For password managers: the account the new password belongs to. -->
			<input type="hidden" name="username" autocomplete="username" value={data.email} />
			<div class="grid gap-2">
				<Label for="password">New password</Label>
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
			<FormError error={form?.error ?? null} />
			<Button type="submit" class="mt-2 h-10 w-full text-sm" disabled={pending}>
				{pending ? 'Saving…' : 'Set password'}
			</Button>
		</form>
	{/if}
</AuthShell>
