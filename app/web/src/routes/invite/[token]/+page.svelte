<script lang="ts">
	import { enhance, type SubmitFunction } from '$app/forms';
	import { page } from '$app/state';
	import AuthDivider from '#lib/components/AuthDivider.svelte';
	import AuthShell from '#lib/components/AuthShell.svelte';
	import FormError from '#lib/components/FormError.svelte';
	import GoogleButton from '#lib/components/GoogleButton.svelte';
	import LogoMark from '#lib/components/LogoMark.svelte';
	import OrgMark from '#lib/components/shell/OrgMark.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { hint } from '#lib/hint.svelte.ts';
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
	<title>{data.invite ? `Join ${data.invite.org_display_name}` : 'Invitation'} · Flock</title>
</svelte:head>

<AuthShell
	title={data.invite ? `Join ${data.invite.org_display_name}` : 'This invitation has expired'}
>
	{#snippet mark()}
		{#if data.invite}
			<span {@attach hint(`Joins as ${as} with ${data.invite.email}`)}>
				<OrgMark
					name={data.invite.org_display_name}
					src={data.invite.org_avatar_url}
					class="size-10 text-[20px]"
				/>
			</span>
		{:else}
			<LogoMark class="size-8" />
		{/if}
	{/snippet}

	{#if !data.invite}
		<Button variant="outline" class="mt-7 h-9 w-full" href="/">Go to Flock</Button>
	{:else}
		{@const invite = data.invite}
		{#if data.user && data.user.email === invite.email}
			<form method="POST" action="?/join" class="mt-7 grid gap-3" use:enhance={submitting}>
				<input type="hidden" name="org" value={invite.org} />
				<FormError error={form?.error ?? null} />
				<Button type="submit" class="h-9 w-full" {pending}>
					{`Join as ${as}`}
				</Button>
			</form>
		{:else if data.user}
			<p
				class="mt-7 rounded-lg bg-muted px-3 py-2 text-[13px] leading-relaxed text-muted-foreground"
			>
				You’re signed in as <span class="text-foreground">{data.user.email}</span>. Sign out to
				accept as {invite.email}.
			</p>
			<form method="POST" action="/logout?{new URLSearchParams({ next: here })}" class="mt-3">
				<Button type="submit" class="h-9 w-full">Sign out and continue</Button>
			</form>
		{:else if invite.account_exists}
			<div class="mt-7 grid gap-3">
				<Button class="h-9 w-full" href={loginHref}>Sign in as {invite.email}</Button>
				{#if data.google}<GoogleButton invite={params.token} />{/if}
			</div>
		{:else}
			{#if data.google}
				<div class="mt-7" {@attach hint(`Use the Google account for ${invite.email}`)}>
					<GoogleButton label="Join with Google" invite={params.token} />
				</div>
				<AuthDivider />
			{/if}

			<form
				method="POST"
				action="?/register"
				class={['grid gap-3', !data.google && 'mt-7']}
				use:enhance={submitting}
			>
				<input type="hidden" name="org" value={invite.org} />
				<div class="grid gap-1.5">
					<Label for="email" class="text-[13px]">Email</Label>
					<Input id="email" value={invite.email} class="h-9" readonly disabled />
				</div>
				<div class="grid gap-1.5">
					<Label for="name" class="text-[13px]">Name</Label>
					<Input
						id="name"
						name="name"
						autocomplete="name"
						value={form && 'name' in form ? form.name : ''}
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
				<FormError error={form?.error ?? null} />
				<Button type="submit" class="mt-2 h-9 w-full" {pending}>Create account and join</Button>
			</form>
		{/if}
	{/if}
</AuthShell>
