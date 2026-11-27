import { error, redirect } from '@sveltejs/kit';
import { loadOrg, orgActions } from '#lib/server/org-settings.ts';
import { orgSettingsHref, type OrgSection } from '#lib/settings.ts';
import type { PageServerLoad } from './$types';

const KNOWN = new Set([
	'general',
	'members',
	'budget',
	'storage',
	'assistant',
	'deleted',
	'import'
]);

export const load: PageServerLoad = async ({ depends, params }) => {
	if (!KNOWN.has(params.section)) error(404, 'Not found');
	const data = await loadOrg({ depends, params });
	// A section this viewer doesn't see (a member on General): the first one they do.
	if (!data.sections.some((s) => s.slug === params.section))
		redirect(307, orgSettingsHref(params.org, data.sections[0].slug as OrgSection));
	return { ...data, section: params.section as OrgSection };
};

export const actions = orgActions;
