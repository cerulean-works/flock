import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';

/**
 * Settings (S10, G1, G2): the account's, an organization's and the server's (super admins), each
 * one page of sections, listed in the sidebar as its table of contents while one is open.
 */

export interface SettingsSection {
	slug: string;
	label: string;
}

export type OrgSection =
	'general' | 'members' | 'budget' | 'storage' | 'assistant' | 'deleted' | 'import';
export type AccountSection = 'profile' | 'organizations' | 'keys' | 'sign-ins' | 'appearance';
export type AdminSection = 'organizations' | 'accounts';

/** What decides which of an org's sections the viewer sees. */
export interface OrgAccess {
	/** An admin of it, or a super admin. */
	admin: boolean;
	/** The server has S3: key lifetimes, model retention and deleted datasets and models. */
	s3: boolean;
	/** Import from W&B: super admins, or anyone while an import is running or just ran. */
	importing: boolean;
}

export function orgSections({ admin, s3, importing }: OrgAccess): SettingsSection[] {
	return [
		admin && { slug: 'general', label: 'General' },
		{ slug: 'members', label: 'Members' },
		{ slug: 'budget', label: 'Budget' },
		admin && s3 && { slug: 'storage', label: 'Storage' },
		admin && { slug: 'assistant', label: 'Assistant' },
		admin && s3 && { slug: 'deleted', label: 'Deleted data' },
		importing && { slug: 'import', label: 'Import from W&B' }
	].filter((s): s is SettingsSection => !!s);
}

export const ACCOUNT_SECTIONS: SettingsSection[] = [
	{ slug: 'profile', label: 'Profile' },
	{ slug: 'organizations', label: 'Organizations' },
	{ slug: 'keys', label: 'Keys' },
	{ slug: 'sign-ins', label: 'Sign-ins' },
	{ slug: 'appearance', label: 'Appearance' }
];

export const ADMIN_SECTIONS: SettingsSection[] = [
	{ slug: 'organizations', label: 'Organizations' },
	{ slug: 'accounts', label: 'Accounts' }
];

/**
 * Old anchors that aren't a section's own id: they land on the section holding them. (Parts of a
 * section keep their old ids, `#budget-warnings` or `#deleted-models`, and land on themselves.)
 */
const ACCOUNT_ALIASES: Record<string, AccountSection> = { 's3-keys': 'keys' };

/** The element an `#anchor` on a settings page stands for. */
export const settingsTarget = (hash: string, area: 'org' | 'account' | 'admin') => {
	const id = decodeURIComponent(hash.replace(/^#/, ''));
	return (area === 'account' && ACCOUNT_ALIASES[id]) || id;
};

const query = (search?: Record<string, string>) =>
	search && Object.keys(search).length ? `?${new URLSearchParams(search)}` : '';

/**
 * An org's settings, at a section, or a part of one (`anchor`) to scroll to: one page per area,
 * the sidebar its table of contents.
 */
export const orgSettingsHref = (org: string, section: OrgSection, anchor?: string) =>
	`${resolve('/(app)/orgs/[org]', { org })}#${anchor ?? section}` as ResolvedPathname;
export const accountHref = (section: AccountSection, search?: Record<string, string>) =>
	`${resolve('/(app)/account')}${query(search)}#${section}` as ResolvedPathname;
export const adminHref = (section: AdminSection) =>
	`${resolve('/(app)/admin')}#${section}` as ResolvedPathname;

/** A section's heading on a settings page (G2): one size for every section. */
export const SETTINGS_HEADING = 'text-[15px] leading-6 font-medium';
/** A part of a section (Invite people, Budget warnings): quieter, under its section's heading. */
export const SETTINGS_SUBHEADING = 'text-[13px] leading-5 font-medium';
