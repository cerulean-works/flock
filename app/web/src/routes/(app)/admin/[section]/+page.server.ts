import { error, redirect } from '@sveltejs/kit';
import { adminActions } from '#lib/server/admin-settings.ts';
import { ADMIN_SECTIONS, adminHref, type AdminSection } from '#lib/settings.ts';
import type { PageServerLoad } from './$types';

/** A section's old page of its own: the super admin page, at that section. */
export const load: PageServerLoad = ({ params, url }) => {
	if (!ADMIN_SECTIONS.some((s) => s.slug === params.section))
		error(404, `settings page ${params.section} not found`);
	const [path, hash] = adminHref(params.section as AdminSection).split('#');
	redirect(307, `${path}${url.search}#${hash}`);
};

export const actions = adminActions;
