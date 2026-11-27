<script lang="ts">
	import { enhance } from '$app/forms';
	import AuthDivider from '#lib/components/AuthDivider.svelte';
	import AuthShell from '#lib/components/AuthShell.svelte';
	import GoogleButton from '#lib/components/GoogleButton.svelte';
	import OrgIdentityFields from '#lib/components/OrgIdentityFields.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import WandbImportFields from '#lib/components/WandbImportFields.svelte';
	import type { PageProps, SubmitFunction } from './$types';

	let { data, form }: PageProps = $props();

	const STEPS = 2;
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

<AuthShell title={step === 0 ? 'Set up Flock' : 'Create your workspace'}>
	<!-- Where the walkthrough is: one bar per step, filled once reached. -->
	<div class="mx-auto mt-4 flex w-16 gap-1.5" role="img" aria-label="Step {step + 1} of {STEPS}">
		{#each { length: STEPS }, i (i)}
			<span class={['h-1 flex-1 rounded-full', i <= step ? 'bg-foreground' : 'bg-border']}></span>
		{/each}
	</div>

	{#if data.step === 'account'}
		{#if data.google}
			<div class="mt-7">
				<GoogleButton label="Set up with Google" />
			</div>
			<AuthDivider />
		{/if}

		<form
			method="POST"
			action="?/account"
			class={['grid gap-3', !data.google && 'mt-7']}
			use:enhance={submit}
		>
			<div class="grid gap-1.5">
				<Label for="name" class="text-[13px]">Name</Label>
				<Input
					id="name"
					name="name"
					autocomplete="name"
					value={form?.name ?? ''}
					class="h-9"
					required
				/>
			</div>
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
					autocomplete="new-password"
					minlength={8}
					class="h-9"
					required
				/>
			</div>
			{#if form?.error}
				<p class="text-[13px] text-destructive" role="alert">
					{form.error.charAt(0).toUpperCase() + form.error.slice(1)}.
				</p>
			{/if}
			<Button type="submit" class="mt-2 h-9 w-full" {pending}>Create account</Button>
		</form>
	{:else}
		<form method="POST" action="?/workspace" class="mt-7 grid gap-3" use:enhance={submit}>
			<OrgIdentityFields bind:displayName bind:id={orgId} large />
			<WandbImportFields org={orgId.trim()} bind:on={importing} entity={form?.entity ?? ''} large />
			{#if form?.error}
				<p class="text-[13px] text-destructive" role="alert">
					{form.error.charAt(0).toUpperCase() + form.error.slice(1)}.
				</p>
			{/if}
			<Button type="submit" class="mt-2 h-9 w-full" {pending}>Create workspace</Button>
		</form>
	{/if}
</AuthShell>
