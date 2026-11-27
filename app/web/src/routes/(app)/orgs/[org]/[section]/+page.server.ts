import { error, redirect } from '@sveltejs/kit';
import { orgActions } from '#lib/server/org-settings.ts';
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

/** A section's old page of its own: the settings page, at that section (its query kept). */
export const load: PageServerLoad = ({ params, url }) => {
	if (!KNOWN.has(params.section)) error(404, `settings page ${params.section} not found`);
	const [path, hash] = orgSettingsHref(params.org, params.section as OrgSection).split('#');
	redirect(307, `${path}${url.search}#${hash}`);
};

// Forms that still post here (`?/…`) keep working.
export const actions = orgActions;
