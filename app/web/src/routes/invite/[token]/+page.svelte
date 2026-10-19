<script lang="ts">
	import { enhance, type SubmitFunction } from '$app/forms';
	import { page } from '$app/state';
	import AuthShell from '#lib/components/AuthShell.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import GoogleButton from '#lib/components/GoogleButton.svelte';
	import OrgMark from '#lib/components/shell/OrgMark.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import type { PageProps } from './$types';

	let { data, form, params }: PageProps = $props();

	const here = $derived(page.url.pathname);
	const loginHref = $derived(`/login?${new URLSearchParams({ next: here })}`);
	const as = $derived(data.invite?.role === 'admin' ? 'an admin' : 'a member');

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
	<title>{data.invite ? `Join ${data.invite.org}` : 'Invitation'} · Flock</title>
</svelte:head>

<AuthShell>
	{#if !data.invite}
		<h1 class="font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">
			This invitation can’t be used
		</h1>
		<p class="mt-3 leading-relaxed text-muted-foreground">
			It was used, has expired, or was withdrawn.
		</p>
		<Button variant="outline" class="mt-8 h-10 w-full text-sm" href="/">Go to Flock</Button>
	{:else}
		{@const invite = data.invite}
		<OrgMark name={invite.org} src={invite.org_avatar_url} class="mb-5 size-12 text-[24px]" />
		<p class="text-[13px] text-muted-foreground">You’re invited to</p>
		<h1 class="mt-1 font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">
			Join {invite.org}
		</h1>
		<p class="mt-3 leading-relaxed text-muted-foreground">
			As {as}, with <span class="text-foreground">{invite.email}</span>.
		</p>

		{#if data.user && data.user.email === invite.email}
			<form method="POST" action="?/join" class="mt-8 grid gap-4" use:enhance={submitting}>
				<input type="hidden" name="org" value={invite.org} />
				<FormError error={form?.error ?? null} />
				<Button type="submit" class="h-10 w-full text-sm" disabled={pending}>
					{pending ? 'Joining…' : `Join ${invite.org}`}
				</Button>
			</form>
		{:else if data.user}
			<p
				class="mt-8 rounded-lg bg-muted px-3 py-2.5 text-[13px] leading-relaxed text-muted-foreground"
			>
				You’re signed in as <span class="text-foreground">{data.user.email}</span>. Sign out to
				accept as {invite.email}.
			</p>
			<form method="POST" action="/logout?{new URLSearchParams({ next: here })}" class="mt-4">
				<Button type="submit" class="h-10 w-full text-sm">Sign out and continue</Button>
			</form>
		{:else if invite.account_exists}
			<div class="mt-8 grid gap-3">
				<Button class="h-10 w-full text-sm" href={loginHref}>Sign in to accept</Button>
				{#if data.google}<GoogleButton invite={params.token} />{/if}
			</div>
			<p class="mt-4 text-[13px] text-muted-foreground">
				{invite.email} already has a Flock account.
			</p>
		{:else}
			{#if data.google}
				<div class="mt-8">
					<GoogleButton label="Join with Google" invite={params.token} />
				</div>
				<div class="my-6 flex items-center gap-3 text-xs text-muted-foreground">
					<span class="h-px flex-1 bg-border"></span>
					or with a password
					<span class="h-px flex-1 bg-border"></span>
				</div>
			{/if}

			<form
				method="POST"
				action="?/register"
				class={['grid gap-4', !data.google && 'mt-8']}
				use:enhance={submitting}
			>
				<input type="hidden" name="org" value={invite.org} />
				<div class="grid gap-2">
					<Label for="email">Email</Label>
					<Input id="email" value={invite.email} class="h-10" readonly disabled />
				</div>
				<div class="grid gap-2">
					<Label for="name">Name</Label>
					<Input
						id="name"
						name="name"
						autocomplete="name"
						value={form && 'name' in form ? form.name : ''}
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
				<FormError error={form?.error ?? null} />
				<Button type="submit" class="mt-2 h-10 w-full text-sm" disabled={pending}>
					{pending ? 'Creating account…' : 'Create account and join'}
				</Button>
			</form>
			{#if data.google}
				<p class="mt-6 text-[13px] text-muted-foreground">
					Use the Google account for {invite.email}.
				</p>
			{/if}
		{/if}
	{/if}
</AuthShell>
