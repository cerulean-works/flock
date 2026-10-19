import { fail, redirect } from '@sveltejs/kit';
import { authConfig, needsWorkspace, refreshUser, signIn } from '#lib/server/auth.ts';
import { change, refused } from '#lib/server/flock.ts';
import { loginHref } from '#lib/server/session.ts';
import type { OrgInfo } from '#lib/types.ts';
import { formText, orgNameError } from '#lib/utils.ts';
import { entityHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * Setting up a fresh server, in two steps: the first account (the super admin), then its
 * first organization. Leaving between them comes back to the second: signing in as a super
 * admin while there is no organization lands here. Once both are done, people sign in (or join
 * from an invitation).
 */
export const load: PageServerLoad = async ({ locals, url }) => {
	const config = await authConfig();
	if (config.signup_open) return { step: 'account' as const, google: config.google };
	if (!locals.token) redirect(303, loginHref(url));
	// Nothing left to set up (or not theirs to): a 401 from Flock signs in again, back here.
	if (!(await needsWorkspace())) redirect(303, '/');
	return { step: 'workspace' as const, google: false };
};

export const actions: Actions = {
	account: async ({ cookies, request }) => {
		const form = await request.formData();
		const name = formText(form, 'name');
		const email = formText(form, 'email');
		const password = String(form.get('password') ?? '');
		const kept = { name, email };
		if (!name) return fail(400, { ...kept, error: 'Enter your name' });
		if (password.length < 8) return fail(400, { ...kept, error: 'Use at least 8 characters' });
		// No organization yet: the next step names it.
		const body = { name, email, password, create_org: false };
		const res = await signIn(cookies, '/api/v1/auth/signup', body, '/setup');
		return fail(res.status, { ...kept, ...res.data });
	},

	/** The first organization, made the super admin's default: on into it. */
	workspace: async ({ request }) => {
		const org = formText(await request.formData(), 'org');
		const invalid = orgNameError(org);
		if (invalid) return fail(400, { org, error: invalid });
		const created = await change<OrgInfo>('POST', '/api/v1/orgs', { name: org });
		if (refused(created)) return fail(created.status, { org, ...created.data });
		const chosen = await change('PATCH', '/api/v1/me', { default_org: org });
		if (refused(chosen)) return fail(chosen.status, { org, ...chosen.data });
		await refreshUser();
		redirect(303, entityHref(org));
	}
};
