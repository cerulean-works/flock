<script lang="ts">
	import Building2Icon from '@lucide/svelte/icons/building-2';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import SunMoonIcon from '@lucide/svelte/icons/sun-moon';
	import TerminalIcon from '@lucide/svelte/icons/terminal';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import UserIcon from '@lucide/svelte/icons/user';
	import UsersIcon from '@lucide/svelte/icons/users';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import type { Component } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import {
		ACCOUNT_SECTIONS,
		ADMIN_SECTIONS,
		accountHref,
		adminHref,
		orgSections,
		orgSettingsHref,
		type AccountSection,
		type AdminSection,
		type OrgSection,
		type SettingsSection
	} from '#lib/settings.ts';
	import { jumpTo, toc } from '#lib/settings-toc.svelte.ts';
	import type { OrgEntry } from '#lib/types.ts';
	import { entityHref } from '#lib/workspace/url.ts';

	interface Props {
		/** The organization in view, whose settings are listed. */
		org: string | null;
		orgs: OrgEntry[];
		superadmin: boolean;
		s3: boolean;
		/** A sidebar row's classes, so these rows match the app's. */
		item: string;
		/** After a jump within the page (the phone drawer closes). */
		onjump?: () => void;
	}

	let { org, orgs, superadmin, s3, item, onjump }: Props = $props();

	/** An entry on the page in view: the section the page is scrolled to marks it. */
	const here = (href: string) => href.split('#')[0].split('?')[0] === page.url.pathname;

	function jump(e: MouseEvent, href: string) {
		if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0 || !here(href)) return;
		if (!jumpTo(href.split('#')[1])) return;
		e.preventDefault();
		onjump?.();
	}

	const ICONS: Record<string, Component> = {
		profile: UserIcon,
		organizations: Building2Icon,
		keys: KeyRoundIcon,
		'sign-ins': TerminalIcon,
		appearance: SunMoonIcon,
		general: SettingsIcon,
		members: UsersIcon,
		budget: WalletIcon,
		storage: HardDriveIcon,
		assistant: SparklesIcon,
		deleted: Trash2Icon,
		import: DownloadIcon,
		accounts: UsersIcon
	};

	const entry = $derived(orgs.find((o) => o.name === org) ?? null);
	/**
	 * The org's sections: as its settings page worked them out while it's open, otherwise from the
	 * viewer's role.
	 */
	const orgList = $derived<SettingsSection[]>(
		!org || (!entry && !superadmin)
			? []
			: page.params.org === org && page.data.sections
				? (page.data.sections as SettingsSection[])
				: orgSections({ admin: entry?.role === 'admin' || superadmin, s3, importing: superadmin })
	);

	const groups = $derived(
		[
			{
				label: 'Account',
				sections: ACCOUNT_SECTIONS,
				href: (s: string) => accountHref(s as AccountSection)
			},
			org &&
				orgList.length && {
					label: entry?.display_name ?? org,
					sections: orgList,
					href: (s: string) => orgSettingsHref(org, s as OrgSection)
				},
			superadmin && {
				label: 'Super admin',
				sections: ADMIN_SECTIONS,
				href: (s: string) => adminHref(s as AdminSection)
			}
		].filter((g) => !!g)
	);
</script>

<!--
	Settings (S10): in place of the app's rows, the way back and every settings section, the table
	of contents of the settings pages (one each for the account, an org and the server). On the
	page in view an entry scrolls to its section, and the section scrolled to is marked.
-->
<div class="grid gap-px">
	<a class={item} href={org ? entityHref(org) : resolve('/(app)')}>
		<ChevronLeftIcon /> Back
	</a>
</div>
{#each groups as group (group.label)}
	<div class="mt-5 flex h-7 items-center pl-2">
		<h2 class="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground">
			{group.label}
		</h2>
	</div>
	<div class="grid gap-px" role="group" aria-label={group.label}>
		{#each group.sections as section (section.slug)}
			{@const href = group.href(section.slug)}
			{@const Icon = ICONS[section.slug]}
			<a
				class={item}
				{href}
				aria-current={here(href) && toc.current === section.slug ? 'page' : undefined}
				onclick={(e) => jump(e, href)}
			>
				<Icon />
				<span class="min-w-0 flex-1 truncate">{section.label}</span>
			</a>
		{/each}
	</div>
{/each}
