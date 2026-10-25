<script lang="ts">
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import ApiKeyCreator from '#lib/components/ApiKeyCreator.svelte';
	import AvatarField from '#lib/components/AvatarField.svelte';
	import HubKeyCreator from '#lib/components/HubKeyCreator.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import FormError from '#lib/components/FormError.svelte';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { dateTime, relativeTime } from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const removal = new Mutation({ success: () => 'API key deleted', toastErrors: true });
	const hubRemoval = new Mutation({ success: () => 'S3 key deleted', toastErrors: true });
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

<div class="flex min-h-0 flex-1 flex-col">
	<main class="min-h-0 flex-1 overflow-y-auto px-5 pb-16">
		<div class="mx-auto max-w-180">
			<h1 class="pt-8 font-serif text-[34px] leading-[1.15] tracking-[-0.02em]">
				{data.profile.name}
			</h1>
			<p class="mt-1 text-muted-foreground">
				{data.profile.email}
				{#if data.profile.is_superadmin}<span>· Super admin</span>{/if}
			</p>

			<section class="mt-10">
				<h2 class="font-serif text-2xl tracking-[-0.01em]">Picture</h2>
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
				<h2 class="font-serif text-2xl tracking-[-0.01em]">Name</h2>
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
				<h2 class="font-serif text-2xl tracking-[-0.01em]">Password</h2>
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
				<h2 class="font-serif text-2xl tracking-[-0.01em]">Organizations</h2>
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
				<h2 class="font-serif text-2xl tracking-[-0.01em]">API keys</h2>
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

			{#if data.hubKeys}
				<section class="mt-10 scroll-mt-6" id="s3-keys">
					<h2 class="font-serif text-2xl tracking-[-0.01em]">S3 keys</h2>
					<p class="mt-2 max-w-[62ch] text-muted-foreground">
						For reading and writing datasets with S3 tools. A key acts with your role, and works
						until deleted.
					</p>
					{#if data.hubKeys.length}
						<ul class="mt-4 border-t border-divider">
							{#each data.hubKeys as key (key.pk)}
								<li class="flex min-h-14 items-center gap-4 border-b border-divider py-2">
									<div class="min-w-0 flex-1">
										<p class="truncate font-medium">{key.name}</p>
										<p class="truncate text-[13px] text-muted-foreground">
											{key.read_only ? 'Read only' : 'Read and write'} ·
											{key.org
												? (data.profile.orgs.find((o) => o.name === key.org)?.display_name ??
													key.org)
												: 'every organization'}
											·
											<span title={key.last_used_at ? dateTime(key.last_used_at) : undefined}>
												{key.last_used_at
													? `used ${relativeTime(key.last_used_at, data.now)}`
													: 'never used'}
											</span>
										</p>
									</div>
									<span class="font-mono text-[12.5px] text-muted-foreground max-sm:hidden"
										>{key.access_key_id}</span
									>
									<span
										class="w-20 text-right text-muted-foreground max-sm:hidden"
										title="Created {dateTime(key.created_at)}"
									>
										{relativeTime(key.created_at, data.now)}
									</span>
									<form method="POST" action="?/deleteHubKey" use:enhance={hubRemoval.enhance}>
										<input type="hidden" name="pk" value={key.pk} />
										<Button
											type="submit"
											variant="ghost"
											size="icon-sm"
											class="text-muted-foreground hover:text-destructive"
											aria-label="Delete S3 key {key.name}"
											title="Delete: tools using it stop working"
										>
											<Trash2Icon />
										</Button>
									</form>
								</li>
							{/each}
						</ul>
					{/if}
					<div class="mt-4">
						<HubKeyCreator orgs={data.profile.orgs} />
					</div>
				</section>
			{/if}

			<section class="mt-10">
				<form method="POST" action="/logout">
					<Button type="submit" variant="outline">Sign out</Button>
				</form>
			</section>
		</div>
	</main>
</div>
