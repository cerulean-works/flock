import { error, redirect } from '@sveltejs/kit';
import { accountActions } from '#lib/server/account-settings.ts';
import { ACCOUNT_SECTIONS, accountHref, type AccountSection } from '#lib/settings.ts';
import type { PageServerLoad } from './$types';

/** A section's old page of its own: the account page, at that section (its query kept). */
export const load: PageServerLoad = ({ params, url }) => {
	if (!ACCOUNT_SECTIONS.some((s) => s.slug === params.section))
		error(404, `settings page ${params.section} not found`);
	redirect(
		307,
		accountHref(params.section as AccountSection, Object.fromEntries(url.searchParams))
	);
};

export const actions = accountActions;
