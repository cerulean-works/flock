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
	import { datasetsHref } from '#lib/datasets.ts';
	import { modelsHref } from '#lib/models.ts';
	import { storageHref } from '#lib/storage.ts';
	import type { OrgEntry } from '#lib/types.ts';
	import { orgHref } from '#lib/workspace/url.ts';
	import OrgMark from './OrgMark.svelte';

	interface Props {
		/** The organization in view, by ID. */
		org: string | null;
		orgs: OrgEntry[];
		/** Where switching lands: this page (home, account) or the org's own runs. */
		stay: boolean;
		/** Just the picture, for the collapsed sidebar's rail. */
		compact?: boolean;
	}

	let { org, orgs, stay, compact = false }: Props = $props();

	const me = $derived(page.data.shell!.me);
	const session = $derived(!!page.data.user);
	const current = $derived(orgs.find((o) => o.name === org));
	// What people call it; its ID only shows in URLs and settings.
	const label = $derived(org ? (current?.display_name ?? org) : null);

	// Menu rows lead with a 24px-wide slot and an 8px gap, so marks, avatars and icons share a column
	// and every label starts at the same place.
	const ROW = 'gap-2';
	const SLOT = 'grid w-6 flex-none place-items-center';

	let form = $state<HTMLFormElement>();
	let signOut = $state<HTMLFormElement>();
	let target = $state('');
	const storage = $derived(!!page.route.id?.startsWith('/(app)/storage/'));
	const datasets = $derived(!!page.route.id?.startsWith('/(app)/datasets/'));
	const models = $derived(!!page.route.id?.startsWith('/(app)/models/'));

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
		aria-label="{label ?? 'No organization'} · {me.name}"
		title={compact ? `${label ?? 'No organization'} · ${me.name}` : undefined}
	>
		<span class="relative flex-none">
			{#if label}
				<OrgMark name={label} src={current?.avatar_url} />
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
				<span class="truncate font-medium">{label ?? me.name}</span>
				<span class="truncate text-xs text-muted-foreground">
					{label ? me.name : 'No organization yet'}
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
					<DropdownMenu.Item class={ROW} onSelect={() => choose(o.name)} title={o.name}>
						<span class={SLOT}>
							<OrgMark name={o.display_name} src={o.avatar_url} class="size-5 text-[11px]" />
						</span>
						<span class="min-w-0 flex-1 truncate">{o.display_name}</span>
						{#if o.name === org}<CheckIcon class="text-muted-foreground" />{/if}
					</DropdownMenu.Item>
				{/each}
				{#if org}
					<DropdownMenu.Item class={ROW}>
						{#snippet child({ props })}
							<a href={orgHref(org)} {...props}>
								<span class={SLOT}><SettingsIcon /></span> Settings and members
							</a>
						{/snippet}
					</DropdownMenu.Item>
				{/if}
			</DropdownMenu.Group>
			<DropdownMenu.Separator />
		{/if}
		<DropdownMenu.Group>
			<DropdownMenu.Label class={['flex items-center font-normal', ROW]}>
				<span class={SLOT}>
					<Avatar name={me.name} src={me.avatar_url} class="size-6 text-[10px]" />
				</span>
				<span class="grid min-w-0 leading-tight">
					<span class="truncate font-medium text-foreground">{me.name}</span>
					<span class="truncate text-xs text-muted-foreground">{me.email}</span>
				</span>
			</DropdownMenu.Label>
			<DropdownMenu.Item class={ROW}>
				{#snippet child({ props })}
					<a href={resolve('/(app)/account')} {...props}>
						<span class={SLOT}><KeyRoundIcon /></span> Account and keys
					</a>
				{/snippet}
			</DropdownMenu.Item>
			{#if me.is_superadmin}
				<DropdownMenu.Item class={ROW}>
					{#snippet child({ props })}
						<a href={resolve('/(app)/admin')} {...props}>
							<span class={SLOT}><ShieldIcon /></span> Super admin
						</a>
					{/snippet}
				</DropdownMenu.Item>
			{/if}
			{#if session}
				<DropdownMenu.Item class={ROW} onSelect={() => signOut?.requestSubmit()}>
					<span class={SLOT}><LogOutIcon /></span> Sign out
				</DropdownMenu.Item>
			{/if}
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>

<form method="POST" action="{resolve('/(app)')}?/workspace" bind:this={form} use:enhance hidden>
	<input type="hidden" name="org" value={target} />
	<!-- Storage goes to the other org's buckets, Datasets to its datasets, Models to its models;
	     other org pages to its runs. -->
	<input
		type="hidden"
		name="next"
		value={storage && target
			? storageHref(target)
			: datasets && target
				? datasetsHref(target)
				: models && target
					? modelsHref(target)
					: stay
						? page.url.pathname + page.url.search
						: ''}
	/>
</form>
<form method="POST" action="/logout" bind:this={signOut} hidden></form>
