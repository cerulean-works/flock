<script lang="ts">
	import { enhance, type SubmitFunction } from '$app/forms';
	import AuthShell from '#lib/components/AuthShell.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { dateTime, relativeTime } from '#lib/utils.ts';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();

	let pending = $state(false);
	const submitting: SubmitFunction = () => {
		pending = true;
		return async ({ update }) => {
			await update({ reset: false });
			pending = false;
		};
	};
</script>

<svelte:head>
	<title>Sign in from the command line · Flock</title>
</svelte:head>

<AuthShell>
	{#if data.step === 'done'}
		<h1 class="text-[34px] leading-[1.15] tracking-[-0.025em]">
			{data.approved ? 'You’re signed in' : 'Sign-in denied'}
		</h1>
		<p class="mt-3 leading-relaxed text-muted-foreground">
			{#if data.approved}
				You can close this tab and return to your terminal.
			{:else}
				The machine wasn’t given access to your account. You can close this tab.
			{/if}
		</p>
	{:else if data.step === 'confirm'}
		{@const request = data.request}
		<p class="text-[13px] text-muted-foreground">Command line sign-in</p>
		<h1 class="mt-1 text-[34px] leading-[1.15] tracking-[-0.025em]">
			Authorize {request.hostname || 'this machine'}?
		</h1>

		<dl class="mt-6 grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-[13px]">
			<dt class="text-muted-foreground">Code</dt>
			<dd class="font-mono">{request.user_code}</dd>
			<dt class="text-muted-foreground">Machine</dt>
			<dd class="truncate">{request.hostname || 'Unnamed'}</dd>
			{#if request.platform}
				<dt class="text-muted-foreground">Platform</dt>
				<dd class="truncate">{request.platform}</dd>
			{/if}
			{#if request.ip}
				<dt class="text-muted-foreground">IP address</dt>
				<dd class="font-mono">{request.ip}</dd>
			{/if}
			<dt class="text-muted-foreground">Asked</dt>
			<dd title={dateTime(request.created_at)}>{relativeTime(request.created_at, data.now)}</dd>
			{#if data.user}
				<dt class="text-muted-foreground">Account</dt>
				<dd class="truncate">{data.user.name} · {data.user.email}</dd>
			{/if}
		</dl>

		<p
			class="mt-6 rounded-lg bg-muted px-3 py-2.5 text-[13px] leading-relaxed text-muted-foreground"
		>
			Authorizing gives this machine access to your Flock account, as you. Only continue if you ran <code
				class="font-mono text-foreground">flock login</code
			> yourself and your terminal shows the same code.
		</p>

		<form method="POST" class="mt-8 grid gap-3" use:enhance={submitting}>
			<input type="hidden" name="code" value={request.user_code} />
			<FormError error={form?.error ?? null} />
			<Button
				type="submit"
				name="approve"
				value="true"
				class="h-10 w-full text-sm"
				disabled={pending}
			>
				Authorize
			</Button>
			<Button
				type="submit"
				name="approve"
				value="false"
				variant="outline"
				class="h-10 w-full text-sm"
				disabled={pending}
			>
				Cancel
			</Button>
		</form>
	{:else}
		<h1 class="text-[34px] leading-[1.15] tracking-[-0.025em]">Sign in from the command line</h1>
		<p class="mt-3 leading-relaxed text-muted-foreground">
			Enter the code that <code class="font-mono text-foreground">flock login</code> shows in your terminal.
		</p>

		<form method="GET" class="mt-8 grid gap-4">
			<div class="grid gap-2">
				<Label for="code">Code</Label>
				<Input
					id="code"
					name="code"
					value={data.code}
					placeholder="WDJB-MJHT"
					autocomplete="off"
					autocapitalize="characters"
					spellcheck={false}
					class="h-10 font-mono tracking-widest uppercase"
					required
				/>
			</div>
			<FormError error={data.error ?? form?.error ?? null} />
			<Button type="submit" class="mt-2 h-10 w-full text-sm">Continue</Button>
		</form>
	{/if}
</AuthShell>
