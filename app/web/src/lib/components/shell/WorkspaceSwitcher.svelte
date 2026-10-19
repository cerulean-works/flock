<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import ChevronsUpDownIcon from '@lucide/svelte/icons/chevrons-up-down';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import LogOutIcon from '@lucide/svelte/icons/log-out';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import { tick } from 'svelte';
	import { enhance } from '$app/forms';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import Avatar from '#lib/components/Avatar.svelte';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.js';
	import type { Role } from '#lib/types.ts';
	import { orgHref } from '#lib/workspace/url.ts';
	import OrgMark from './OrgMark.svelte';

	interface Props {
		org: string | null;
		orgs: { name: string; role: Role; avatar_url: string | null }[];
		/** Where switching lands: this page (home, account) or the org's own runs. */
		stay: boolean;
		/** Just the picture, for the collapsed sidebar's rail. */
		compact?: boolean;
	}

	let { org, orgs, stay, compact = false }: Props = $props();

	const me = $derived(page.data.shell!.me);
	// FLOCK_API_KEY acts as someone without a session: nothing to sign out of.
	const session = $derived(!!page.data.user);
	const current = $derived(orgs.find((o) => o.name === org));

	let form = $state<HTMLFormElement>();
	let signOut = $state<HTMLFormElement>();
	let target = $state('');

	async function choose(name: string) {
		if (name === org) return;
		target = name;
		await tick(); // the hidden field has the name before the form is read
		form?.requestSubmit();
	}
</script>

<!-- The organization, and who is signed in: switching, its settings, and the account. -->
<DropdownMenu.Root>
	<DropdownMenu.Trigger
		class={[
			'group flex items-center gap-3 rounded-lg text-left outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:bg-muted',
			compact ? 'p-1.5' : 'w-full px-2 py-2.5'
		]}
		aria-label="{org ?? 'No organization'} · {me.name}"
		title={compact ? `${org ?? 'No organization'} · ${me.name}` : undefined}
	>
		<span class="relative flex-none">
			{#if org}
				<OrgMark name={org} src={current?.avatar_url} />
				<!-- Who is signed in: a badge on the organization's corner, ringed in the
				     background so it reads as separate from the mark. -->
				<Avatar
					name={me.name}
					src={me.avatar_url}
					class="absolute -right-1.5 -bottom-1.5 size-4 text-[7px] ring-2 ring-sidebar group-hover:ring-muted group-aria-expanded:ring-muted"
				/>
			{:else}
				<Avatar name={me.name} src={me.avatar_url} />
			{/if}
		</span>
		{#if !compact}
			<span class="grid min-w-0 flex-1 leading-tight">
				<span class="truncate font-medium">{org ?? me.name}</span>
				<span class="truncate text-xs text-muted-foreground">
					{org ? me.name : 'No organization yet'}
				</span>
			</span>
			<ChevronsUpDownIcon class="size-4 flex-none text-muted-foreground" />
		{/if}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content
		side={compact ? 'right' : 'top'}
		align={compact ? 'end' : 'start'}
		class={compact ? 'w-64' : 'w-(--bits-dropdown-menu-anchor-width) min-w-60'}
	>
		{#if orgs.length}
			<DropdownMenu.Group>
				<DropdownMenu.Label class="text-xs font-normal text-muted-foreground">
					Organizations
				</DropdownMenu.Label>
				{#each orgs as o (o.name)}
					<DropdownMenu.Item onSelect={() => choose(o.name)}>
						<OrgMark name={o.name} src={o.avatar_url} class="size-5 text-[11px]" />
						<span class="min-w-0 flex-1 truncate">{o.name}</span>
						{#if o.name === org}<CheckIcon class="text-muted-foreground" />{/if}
					</DropdownMenu.Item>
				{/each}
				{#if org}
					<DropdownMenu.Item>
						{#snippet child({ props })}
							<a href={orgHref(org)} {...props}><SettingsIcon /> Settings and members</a>
						{/snippet}
					</DropdownMenu.Item>
				{/if}
			</DropdownMenu.Group>
			<DropdownMenu.Separator />
		{/if}
		<DropdownMenu.Group>
			<DropdownMenu.Label class="flex items-center gap-2.5 font-normal">
				<Avatar name={me.name} src={me.avatar_url} class="size-6 text-[10px]" />
				<span class="grid min-w-0 leading-tight">
					<span class="truncate font-medium text-foreground">{me.name}</span>
					<span class="truncate text-xs text-muted-foreground">{me.email}</span>
				</span>
			</DropdownMenu.Label>
			<DropdownMenu.Item>
				{#snippet child({ props })}
					<a href={resolve('/(app)/account')} {...props}><KeyRoundIcon /> Account and API keys</a>
				{/snippet}
			</DropdownMenu.Item>
			{#if me.is_superadmin}
				<DropdownMenu.Item>
					{#snippet child({ props })}
						<a href={resolve('/(app)/admin')} {...props}><ShieldIcon /> Super admin</a>
					{/snippet}
				</DropdownMenu.Item>
			{/if}
			{#if session}
				<DropdownMenu.Item onSelect={() => signOut?.requestSubmit()}>
					<LogOutIcon /> Sign out
				</DropdownMenu.Item>
			{/if}
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>

<form method="POST" action="{resolve('/(app)')}?/workspace" bind:this={form} use:enhance hidden>
	<input type="hidden" name="org" value={target} />
	<input type="hidden" name="next" value={stay ? page.url.pathname + page.url.search : ''} />
</form>
<form method="POST" action="/logout" bind:this={signOut} hidden></form>
