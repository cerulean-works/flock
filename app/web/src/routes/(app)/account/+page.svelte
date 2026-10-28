<script lang="ts">
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import KeysSection from '#lib/components/KeysSection.svelte';
	import ProfileHeader from '#lib/components/ProfileHeader.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { dateTime, relativeTime } from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import Page from '#lib/components/Page.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const revocation = new Mutation({ success: () => 'Machine signed out', toastErrors: true });
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

<Page width="home">
	<ProfileHeader profile={data.profile} />

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

	<KeysSection keys={data.keys} s3={data.s3} defaultOrg={data.profile.default_org} now={data.now} />

	<section class="mt-10">
		<h2 class="text-2xl tracking-[-0.01em]">Command line sign-ins</h2>
		<p class="mt-2 max-w-[62ch] text-muted-foreground">
			Machines signed in with <code class="font-mono text-[0.9em]">flock login</code>. A sign-in
			expires after 90 days without use. Revoking one signs that machine out.
		</p>
		{#if data.cliSessions.length}
			<ul class="mt-4 border-t border-divider">
				{#each data.cliSessions as session (session.id)}
					{@const name = session.name || 'Unnamed machine'}
					<li class="flex h-12 items-center gap-4 border-b border-divider">
						<span class="min-w-0 flex-1 truncate font-medium">{name}</span>
						<span class="text-muted-foreground" title="Signed in {dateTime(session.created_at)}">
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
								title="Sign this machine out"
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
</Page>
