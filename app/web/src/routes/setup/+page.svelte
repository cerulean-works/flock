<script lang="ts">
	import { enhance } from '$app/forms';
	import AuthShell from '#lib/components/AuthShell.svelte';
	import GoogleButton from '#lib/components/GoogleButton.svelte';
	import OrgIdentityFields from '#lib/components/OrgIdentityFields.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import WandbImportFields from '#lib/components/WandbImportFields.svelte';
	import type { PageProps, SubmitFunction } from './$types';

	let { data, form }: PageProps = $props();

	const STEPS = ['Your account', 'Your workspace'];
	const step = $derived(data.step === 'account' ? 0 : 1);

	let displayName = $derived(form?.displayName ?? '');
	let orgId = $derived(form?.org ?? '');
	let importing = $derived(!!form?.wandb);
	let pending = $state(false);
	const submit: SubmitFunction = () => {
		pending = true;
		return async ({ update }) => {
			await update({ reset: false });
			pending = false;
		};
	};
</script>

<svelte:head>
	<title>Set up Flock</title>
</svelte:head>

<AuthShell>
	<!-- Where the walkthrough is: one bar per step, filled once reached. -->
	<div class="mb-6">
		<div class="flex gap-1.5" aria-hidden="true">
			{#each STEPS as label, i (label)}
				<span class={['h-1 flex-1 rounded-full', i <= step ? 'bg-foreground' : 'bg-border']}></span>
			{/each}
		</div>
		<p class="mt-2.5 text-[13px] text-muted-foreground">
			Step {step + 1} of {STEPS.length} · {STEPS[step]}
		</p>
	</div>

	{#if data.step === 'account'}
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
			action="?/account"
			class={['grid gap-4', !data.google && 'mt-8']}
			use:enhance={submit}
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
	{:else}
		<h1 class="font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">Create your workspace</h1>
		<p class="mt-3 leading-relaxed text-muted-foreground">
			An organization holds projects and runs, and the people you invite to them. You can add more
			later.
		</p>

		<form method="POST" action="?/workspace" class="mt-8 grid gap-4" use:enhance={submit}>
			<OrgIdentityFields bind:displayName bind:id={orgId} large />
			<WandbImportFields org={orgId.trim()} bind:on={importing} entity={form?.entity ?? ''} large />
			{#if form?.error}
				<p class="text-[13px] text-destructive" role="alert">
					{form.error.charAt(0).toUpperCase() + form.error.slice(1)}.
				</p>
			{/if}
			<Button type="submit" class="mt-2 h-10 w-full text-sm" disabled={pending}>
				{pending ? 'Creating workspace…' : 'Create workspace'}
			</Button>
		</form>
	{/if}
</AuthShell>
