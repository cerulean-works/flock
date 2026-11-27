<script lang="ts">
	import { hint } from '#lib/hint.svelte.ts';
	import MonitorIcon from '@lucide/svelte/icons/monitor';
	import MoonIcon from '@lucide/svelte/icons/moon';
	import SunIcon from '@lucide/svelte/icons/sun';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { enhance } from '$app/forms';
	import KeysSection from '#lib/components/KeysSection.svelte';
	import ProfileSettings from '#lib/components/ProfileSettings.svelte';
	import SettingsPage from '#lib/components/SettingsPage.svelte';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Mutation } from '#lib/data/mutation.svelte.ts';
	import { ACCOUNT_SECTIONS, accountHref, oldAccountAnchor } from '#lib/settings.ts';
	import type { Theme } from '#lib/theme.ts';
	import { setTheme } from '#lib/theme.svelte.ts';
	import { dateTime, relativeTime } from '#lib/utils.ts';
	import { entityHref } from '#lib/workspace/url.ts';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const title = $derived(ACCOUNT_SECTIONS.find((s) => s.slug === data.section)?.label ?? 'Account');

	const revocation = new Mutation({ success: () => 'Machine signed out', toastErrors: true });
	const defaultOrg = new Mutation<{ org: string }>({
		success: ({ org }) => {
			const name = data.profile.orgs.find((o) => o.name === org)?.display_name ?? org;
			return `wandb.init now logs to ${name} by default`;
		},
		toastErrors: true
	});

	const appearances = [
		{ value: 'light', label: 'Light', icon: SunIcon },
		{ value: 'dark', label: 'Dark', icon: MoonIcon },
		{ value: 'system', label: 'System', icon: MonitorIcon }
	] as const satisfies { value: Theme; label: string; icon: unknown }[];

	let theme = $derived(data.theme);
</script>

<svelte:head>
	<title>{title} · Flock</title>
</svelte:head>

<SettingsPage
	{title}
	trail={[{ label: 'Account', href: accountHref('profile') }]}
	follow={(hash) => {
		const to = oldAccountAnchor(hash);
		return to && accountHref(to);
	}}
>
	{#if data.section === 'profile'}
		<ProfileSettings profile={data.profile} />
	{:else if data.section === 'organizations'}
		<section>
			{#if data.profile.orgs.length}
				<ul class="mt-3 border-t border-divider">
					{#each data.profile.orgs as org (org.name)}
						<li class="flex h-12 items-center gap-3 border-b border-divider">
							<a
								class="min-w-0 truncate font-medium hover:text-link"
								href={entityHref(org.name)}
								{@attach hint(org.name)}>{org.display_name}</a
							>
							<Badge variant="secondary" class="text-muted-foreground">
								{org.role === 'admin' ? 'Admin' : 'Member'}
							</Badge>
							{#if org.name === data.profile.default_org}
								<span class="ml-auto text-xs text-muted-foreground">
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
										{@attach hint('Log runs here when wandb.init names no entity')}
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
	{:else if data.section === 'keys'}
		<KeysSection
			keys={data.keys}
			s3={data.s3}
			defaultOrg={data.profile.default_org}
			now={data.now}
		/>
	{:else if data.section === 'sign-ins'}
		<section>
			<p class="mt-2 text-muted-foreground">
				Machines signed in with <code class="font-mono text-[0.9em]">flock login</code>.
			</p>
			<table class="mt-4 w-full table-fixed border-collapse text-[13px]">
				<thead>
					<tr class="h-9 border-b border-divider text-left text-xs text-muted-foreground">
						<th class="pr-2 font-normal">Machine</th>
						<th class="hidden w-40 px-2 font-normal sm:table-cell">Signed in</th>
						<th class="w-32 px-2 font-normal">Last used</th>
						<th class="w-12"><span class="sr-only">Revoke</span></th>
					</tr>
				</thead>
				<tbody>
					{#each data.cliSessions as session (session.id)}
						{@const name = session.name || 'Unnamed machine'}
						<tr class="h-11 border-b border-divider">
							<td class="truncate pr-2">{name}</td>
							<td class="hidden px-2 text-muted-foreground sm:table-cell">
								{dateTime(session.created_at)}
							</td>
							<td class="px-2 text-muted-foreground">
								{session.last_used_at === null
									? 'Not used yet'
									: relativeTime(session.last_used_at, data.now)}
							</td>
							<td class="text-right">
								<form method="POST" action="?/revokeCli" use:enhance={revocation.enhance}>
									<input type="hidden" name="id" value={session.id} />
									<Button
										type="submit"
										variant="ghost"
										size="icon-sm"
										class="text-muted-foreground hover:text-destructive"
										aria-label="Revoke the sign-in of {name}"
										{@attach hint('Sign this machine out')}
									>
										<Trash2Icon />
									</Button>
								</form>
							</td>
						</tr>
					{:else}
						<tr class="h-11 border-b border-divider">
							<td colspan="4" class="pr-2 text-muted-foreground">
								None yet. Run <code class="font-mono text-[0.9em]">flock login</code> to sign in from
								a terminal.
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</section>
	{:else if data.section === 'appearance'}
		<section>
			<p class="mt-2 text-muted-foreground">
				System follows your computer’s light or dark setting. It’s saved in this browser.
			</p>
			<div
				role="radiogroup"
				aria-label="Appearance"
				class="mt-4 inline-flex gap-1 rounded-lg border p-1"
			>
				{#each appearances as { value, label, icon: Icon } (value)}
					<label
						class="flex h-7 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-muted-foreground transition-colors select-none hover:text-foreground has-checked:bg-muted has-checked:text-foreground has-focus-visible:ring-3 has-focus-visible:ring-ring/50 [&_svg]:size-4"
					>
						<input
							type="radio"
							name="theme"
							class="sr-only"
							{value}
							checked={theme === value}
							onchange={() => ((theme = value), setTheme(value))}
						/>
						<Icon />
						{label}
					</label>
				{/each}
			</div>
		</section>
	{/if}
</SettingsPage>
