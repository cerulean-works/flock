<script lang="ts">
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import ApiKeyCreator from '#lib/components/ApiKeyCreator.svelte';
	import AvatarField from '#lib/components/AvatarField.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import FormError from '#lib/components/FormError.svelte';
	import S3KeyCreator from '#lib/components/S3KeyCreator.svelte';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { groupKeys } from '#lib/s3.ts';
	import { count, dateTime, duration, relativeTime } from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const removal = new Mutation({ success: () => 'API key deleted', toastErrors: true });
	const s3Revocation = new Mutation<{ revokedS3: number }>({
		success: ({ revokedS3 }) =>
			revokedS3 === 1 ? 'S3 key revoked' : `${revokedS3} S3 keys revoked`,
		toastErrors: true
	});
	const orgName = (name: string | null) =>
		data.s3?.orgs.find((o) => o.name === name)?.display_name ?? name ?? 'a deleted organization';
	const revocation = new Mutation({ success: () => 'Machine signed out', toastErrors: true });
	const profile = new Mutation({ success: () => 'Name saved' });
	let passwordForm = $state<HTMLFormElement>();
	const password = new Mutation({
		success: () => (data.profile.has_password ? 'Password changed' : 'Password set'),
		done: () => passwordForm?.reset()
	});
	const defaultOrg = new Mutation<{ org: string }>({
		success: ({ org }) => {
			const name = data.profile.orgs.find((o) => o.name === org)?.display_name ?? org;
			return `wandb.init now logs to ${name} by default`;
		},
		toastErrors: true
	});
</script>

<svelte:head>
	<title>Account · Flock</title>
</svelte:head>

{#snippet revoke(ids: number[], label: string)}
	<form method="POST" action="?/deleteS3Keys" use:enhance={s3Revocation.enhance}>
		{#each ids as id (id)}<input type="hidden" name="id" value={id} />{/each}
		<Button
			type="submit"
			variant="ghost"
			size="icon-sm"
			class="text-muted-foreground hover:text-destructive"
			aria-label={label}
			title="Revoke: the proxy refuses it from its next request"
		>
			<Trash2Icon />
		</Button>
	</form>
{/snippet}

<div class="flex min-h-0 flex-1 flex-col">
	<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
		<div class="mx-auto max-w-180">
			<h1 class="pt-8 text-[34px] leading-[1.15] tracking-[-0.025em]">
				{data.profile.name}
			</h1>
			<p class="mt-1 text-muted-foreground">
				{data.profile.email}
				{#if data.profile.is_superadmin}<span>· Super admin</span>{/if}
			</p>

			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">Picture</h2>
				<div class="mt-4">
					<AvatarField
						kind="user"
						name={data.profile.name}
						src={data.profile.avatar_url}
						gravatar={data.profile.avatar_source !== 'gravatar'}
					/>
				</div>
			</section>

			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">Name</h2>
				<p class="mt-2 max-w-[62ch] text-muted-foreground">
					What others see on your runs and in member lists.
				</p>
				<form
					method="POST"
					action="?/profile"
					class="mt-4 grid max-w-100 gap-3"
					use:enhance={profile.enhance}
				>
					<div class="flex gap-2">
						<Input
							name="name"
							value={data.profile.name}
							aria-label="Name"
							autocomplete="name"
							maxlength={128}
							required
						/>
						<Button type="submit" variant="outline" disabled={profile.pending}>
							{profile.pending ? 'Saving…' : 'Save'}
						</Button>
					</div>
					<FormError error={profile.error} />
				</form>
			</section>

			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">Password</h2>
				<p class="mt-2 max-w-[62ch] text-muted-foreground">
					{#if data.profile.has_password}
						Changing it signs you out everywhere else.
					{:else if data.profile.google}
						You sign in with Google. Set a password to sign in with your email too.
					{:else}
						Set a password to sign in with your email.
					{/if}
				</p>
				<form
					bind:this={passwordForm}
					method="POST"
					action="?/password"
					class="mt-4 grid max-w-100 gap-4"
					use:enhance={password.enhance}
				>
					<!-- For password managers: the account the password belongs to. -->
					<input
						type="email"
						name="email"
						value={data.profile.email}
						autocomplete="username"
						hidden
						readonly
					/>
					{#if data.profile.has_password}
						<div class="grid gap-2">
							<Label for="current-password">Current password</Label>
							<Input
								id="current-password"
								name="current"
								type="password"
								autocomplete="current-password"
								required
							/>
						</div>
					{/if}
					<div class="grid gap-2">
						<Label for="new-password">New password</Label>
						<Input
							id="new-password"
							name="new"
							type="password"
							autocomplete="new-password"
							minlength={8}
							required
						/>
						<p class="text-xs text-muted-foreground">At least 8 characters.</p>
					</div>
					<div class="grid gap-2">
						<Label for="confirm-password">Confirm new password</Label>
						<Input
							id="confirm-password"
							name="confirm"
							type="password"
							autocomplete="new-password"
							required
						/>
					</div>
					<FormError error={password.error} />
					<div>
						<Button type="submit" variant="outline" disabled={password.pending}>
							{password.pending
								? 'Saving…'
								: data.profile.has_password
									? 'Change password'
									: 'Set password'}
						</Button>
					</div>
				</form>
			</section>

			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">Organizations</h2>
				{#if data.profile.orgs.length}
					<ul class="mt-3 border-t border-divider">
						{#each data.profile.orgs as org (org.name)}
							<li class="flex h-12 items-center gap-3 border-b border-divider">
								<a
									class="min-w-0 truncate font-medium hover:text-link"
									href={entityHref(org.name)}
									title={org.name}>{org.display_name}</a
								>
								<Badge variant="secondary" class="text-muted-foreground">
									{org.role === 'admin' ? 'Admin' : 'Member'}
								</Badge>
								{#if org.name === data.profile.default_org}
									<span class="text-xs text-muted-foreground">
										default for <code class="font-mono">wandb.init</code>
									</span>
								{:else}
									<form
										method="POST"
										action="?/defaultOrg"
										class="ml-auto"
										use:enhance={defaultOrg.enhance}
									>
										<input type="hidden" name="org" value={org.name} />
										<Button
											type="submit"
											variant="ghost"
											size="sm"
											class="text-muted-foreground"
											disabled={defaultOrg.pending}
											title="Log runs here when wandb.init names no entity"
										>
											Make default
										</Button>
									</form>
								{/if}
							</li>
						{/each}
					</ul>
				{:else}
					<p class="mt-3 text-muted-foreground">None yet.</p>
				{/if}
			</section>

			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">API keys</h2>
				<p class="mt-2 max-w-[62ch] text-muted-foreground">Keys work until deleted.</p>
				{#if data.keys.length}
					<ul class="mt-4 border-t border-divider">
						{#each data.keys as key (key.id)}
							<li class="flex h-12 items-center gap-4 border-b border-divider">
								<span class="min-w-0 flex-1 truncate font-medium">{key.name}</span>
								<span class="font-mono text-[12.5px] text-muted-foreground">{key.prefix}…</span>
								<span class="w-24 text-muted-foreground" title={dateTime(key.created_at)}>
									{relativeTime(key.created_at, data.now)}
								</span>
								<form method="POST" action="?/deleteKey" use:enhance={removal.enhance}>
									<input type="hidden" name="id" value={key.id} />
									<Button
										type="submit"
										variant="ghost"
										size="icon-sm"
										class="text-muted-foreground hover:text-destructive"
										aria-label="Delete key {key.name}"
										title="Delete: scripts using it stop working"
									>
										<Trash2Icon />
									</Button>
								</form>
							</li>
						{/each}
					</ul>
				{/if}
				<div class="mt-4">
					<ApiKeyCreator named />
				</div>
			</section>

			{#if data.s3}
				{@const groups = groupKeys(data.s3.keys)}
				<section class="mt-10" id="s3-keys">
					<h2 class="text-2xl tracking-[-0.01em]">S3 keys</h2>
					<p class="mt-2 max-w-[62ch] text-muted-foreground">
						For your organizations’ buckets, at
						<span class="font-mono text-[0.9em]">{data.s3.endpoint}</span>. Scripts and jobs should
						run
						<code class="font-mono text-[0.9em]">flock s3 credentials</code>, whose keys renew
						themselves; make a key here for tools that can’t.
					</p>
					{#if data.s3.keys.length}
						<ul class="mt-4 border-t border-divider">
							{#each groups.longLived as key (key.id)}
								<li class="flex min-h-12 items-center gap-4 border-b border-divider py-2">
									<div class="min-w-0 flex-1">
										<p class="truncate font-medium">{key.name}</p>
										<p class="truncate text-[13px] text-muted-foreground">
											<span class="font-mono text-[12.5px]">{key.access_key_id}</span>
											· {orgName(key.org)}{key.read_only ? ' · read-only' : ''}
										</p>
									</div>
									<span class="hidden w-36 text-[13px] text-muted-foreground sm:block">
										{key.expires_at === null
											? 'Never expires'
											: `${duration(key.expires_at - data.now)} left`}
										<br />
										{key.last_used_at === null
											? 'Not used yet'
											: `Used ${relativeTime(key.last_used_at, data.now)}`}
									</span>
									{@render revoke([key.id], `Revoke S3 key ${key.name}`)}
								</li>
							{/each}
							{#each groups.ephemeral as group (group.id)}
								{@const used = Math.max(...group.keys.map((k) => k.last_used_at ?? -Infinity))}
								<li class="flex min-h-12 items-center gap-4 border-b border-divider py-2">
									<div class="min-w-0 flex-1">
										<p class="truncate">
											{count(group.keys.length, 'ephemeral key')} from
											<span class="font-medium">{group.source}</span>
										</p>
										<p class="truncate text-[13px] text-muted-foreground">
											{group.kind === 'machine'
												? 'Issued to a command line sign-in'
												: 'Issued to an API key'} ·
											{[...new Set(group.keys.map((k) => orgName(k.org)))].join(', ')}
										</p>
									</div>
									<span class="hidden w-36 text-[13px] text-muted-foreground sm:block">
										{Number.isFinite(used)
											? `Used ${relativeTime(used, data.now)}`
											: 'Not used yet'}
									</span>
									{@render revoke(
										group.keys.map((k) => k.id),
										`Revoke the ephemeral keys from ${group.source}`
									)}
								</li>
							{/each}
						</ul>
					{/if}
					<div class="mt-4">
						{#if data.s3.orgs.length}
							<S3KeyCreator orgs={data.s3.orgs} initialOrg={data.profile.default_org} />
						{:else}
							<p class="text-muted-foreground">Join an organization to use its buckets.</p>
						{/if}
					</div>
				</section>
			{/if}

			<section class="mt-10">
				<h2 class="text-2xl tracking-[-0.01em]">Command line sign-ins</h2>
				<p class="mt-2 max-w-[62ch] text-muted-foreground">
					Machines signed in with <code class="font-mono text-[0.9em]">flock login</code>. Each
					lasts until 90 days without use; revoking one signs that machine out.
				</p>
				{#if data.cliSessions.length}
					<ul class="mt-4 border-t border-divider">
						{#each data.cliSessions as session (session.id)}
							{@const name = session.name || 'Unnamed machine'}
							<li class="flex h-12 items-center gap-4 border-b border-divider">
								<span class="min-w-0 flex-1 truncate font-medium">{name}</span>
								<span
									class="text-muted-foreground"
									title="Signed in {dateTime(session.created_at)}"
								>
									{session.last_used_at === null
										? 'Not used yet'
										: `Used ${relativeTime(session.last_used_at, data.now)}`}
								</span>
								<form method="POST" action="?/revokeCli" use:enhance={revocation.enhance}>
									<input type="hidden" name="id" value={session.id} />
									<Button
										type="submit"
										variant="ghost"
										size="icon-sm"
										class="text-muted-foreground hover:text-destructive"
										aria-label="Revoke the sign-in of {name}"
										title="Revoke: this machine is signed out"
									>
										<Trash2Icon />
									</Button>
								</form>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="mt-3 text-muted-foreground">
						None yet. Run <code class="font-mono text-[0.9em]">flock login</code> to sign in from a terminal.
					</p>
				{/if}
			</section>

			<section class="mt-10">
				<form method="POST" action="/logout">
					<Button type="submit" variant="outline">Sign out</Button>
				</form>
			</section>
		</div>
	</main>
</div>
