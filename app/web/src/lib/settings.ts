import { resolve } from '$app/paths';
import type { ResolvedPathname } from '$app/types';

/**
 * Settings (S10, G1, G2): the account's, an organization's and the server's (super admins), each
 * section a page of its own, listed in the sidebar while one of them is open (Linear's settings).
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

/** The anchors of the one-page settings before sections had pages, and where they went. */
const OLD_ORG_ANCHORS: Record<string, OrgSection> = {
	budget: 'budget',
	'budget-warnings': 'budget',
	'deleted-projects': 'budget',
	's3-keys': 'storage',
	'model-retention': 'storage',
	assistant: 'assistant',
	'deleted-datasets': 'deleted',
	'deleted-models': 'deleted',
	import: 'import'
};
const OLD_ACCOUNT_ANCHORS: Record<string, AccountSection> = {
	keys: 'keys',
	's3-keys': 'keys'
};

/** The section an old `#anchor` stood for, if any. */
export function oldOrgAnchor(hash: string): OrgSection | null {
	return OLD_ORG_ANCHORS[hash.replace(/^#/, '')] ?? null;
}
export function oldAccountAnchor(hash: string): AccountSection | null {
	return OLD_ACCOUNT_ANCHORS[hash.replace(/^#/, '')] ?? null;
}

/** An org's settings section, and a part of it (`anchor`) to scroll to. */
export const orgSettingsHref = (org: string, section: OrgSection, anchor?: string) =>
	`${resolve('/(app)/orgs/[org]/[section]', { org, section })}${anchor ? `#${anchor}` : ''}` as ResolvedPathname;
export const accountHref = (section: AccountSection) =>
	resolve('/(app)/account/[section]', { section }) as ResolvedPathname;
export const adminHref = (section: AdminSection) =>
	resolve('/(app)/admin/[section]', { section }) as ResolvedPathname;

/** A section heading on a settings page (G2): one size for every section. */
export const SETTINGS_HEADING = 'text-[15px] leading-6 font-medium';
