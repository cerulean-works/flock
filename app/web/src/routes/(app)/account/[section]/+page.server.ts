import { error } from '@sveltejs/kit';
import { accountActions, loadAccount } from '#lib/server/account-settings.ts';
import { ACCOUNT_SECTIONS, type AccountSection } from '#lib/settings.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params }) => {
	if (!ACCOUNT_SECTIONS.some((s) => s.slug === params.section)) error(404, 'Not found');
	return { ...(await loadAccount({ locals })), section: params.section as AccountSection };
};

export const actions = accountActions;
