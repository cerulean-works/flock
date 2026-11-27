import { error } from '@sveltejs/kit';
import { adminActions, loadAdmin } from '#lib/server/admin-settings.ts';
import { ADMIN_SECTIONS, type AdminSection } from '#lib/settings.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params }) => {
	if (!ADMIN_SECTIONS.some((s) => s.slug === params.section))
		error(404, `settings page ${params.section} not found`);
	return { ...(await loadAdmin()), section: params.section as AdminSection };
};

export const actions = adminActions;
