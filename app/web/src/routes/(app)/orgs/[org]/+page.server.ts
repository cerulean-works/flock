import { redirect } from '@sveltejs/kit';
import { flock } from '#lib/server/flock.ts';
import { orgActions } from '#lib/server/org-settings.ts';
import { orgSettingsHref } from '#lib/settings.ts';
import type { OrgInfo } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

/**
 * Settings open on their first section: General for admins, Members for everyone else. An old
 * link's `#anchor` survives the redirect, and the section page follows it to its own page.
 */
export const load: PageServerLoad = async ({ params }) => {
	const org = (await flock(`/api/v1/orgs/${encodeURIComponent(params.org)}`).then((r) =>
		r.json()
	)) as OrgInfo;
	redirect(307, orgSettingsHref(params.org, org.role === 'admin' ? 'general' : 'members'));
};

// Forms that still post here (`?/…`) keep working.
export const actions = orgActions;
