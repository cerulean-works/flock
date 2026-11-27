<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
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

<AuthShell
	title={data.step === 'done'
		? data.approved
			? 'You’re signed in'
			: 'Sign-in denied'
		: data.step === 'confirm'
			? `Authorize ${data.request.hostname || 'this machine'}?`
			: 'Sign in from the command line'}
>
	{#if data.step === 'confirm'}
		{@const request = data.request}
		<dl class="mt-7 grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-[13px]">
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
			<dd {@attach hint(dateTime(request.created_at))}>
				{relativeTime(request.created_at, data.now)}
			</dd>
			{#if data.user}
				<dt class="text-muted-foreground">Account</dt>
				<dd class="truncate">
					{data.user.name} <span class="ml-1 text-muted-foreground">{data.user.email}</span>
				</dd>
			{/if}
		</dl>

		<p class="mt-6 rounded-lg bg-muted px-3 py-2 text-[13px] leading-relaxed text-muted-foreground">
			Only authorize if you ran <code class="font-mono text-foreground">flock login</code> yourself and
			your terminal shows the same code. This machine will act as you.
		</p>

		<form method="POST" class="mt-6 grid gap-3" use:enhance={submitting}>
			<input type="hidden" name="code" value={request.user_code} />
			<FormError error={form?.error ?? null} />
			<Button type="submit" name="approve" value="true" class="h-9 w-full" disabled={pending}>
				Authorize
			</Button>
			<Button
				type="submit"
				name="approve"
				value="false"
				variant="outline"
				class="h-9 w-full"
				disabled={pending}
			>
				Cancel
			</Button>
		</form>
	{:else if data.step !== 'done'}
		<form method="GET" class="mt-7 grid gap-3">
			<div class="grid gap-1.5">
				<Label for="code" class="text-[13px]"
					>Code from <code class="font-mono">flock login</code></Label
				>
				<Input
					id="code"
					name="code"
					value={data.code}
					placeholder="WDJB-MJHT"
					autocomplete="off"
					autocapitalize="characters"
					spellcheck={false}
					class="h-9 font-mono tracking-widest uppercase placeholder:tracking-widest"
					required
				/>
			</div>
			<FormError error={data.error ?? form?.error ?? null} />
			<Button type="submit" class="mt-2 h-9 w-full">Continue</Button>
		</form>
	{/if}
</AuthShell>
