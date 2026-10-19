import { fail, isHttpError, redirect } from '@sveltejs/kit';
import { authConfig, refreshUser, signIn } from '#lib/server/auth.ts';
import { change, refused, flock } from '#lib/server/flock.ts';
import type { Role } from '#lib/types.ts';
import { formText } from '#lib/utils.ts';
import { entityHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

export interface InviteDetails {
	org: string;
	email: string;
	role: Role;
	expires_at: number;
	/** The invited email has an account already: it signs in to accept. */
	account_exists: boolean;
	/** The organization's picture, or null. */
	org_avatar_url: string | null;
}

const invitePath = (token: string) => `/api/v1/invites/${encodeURIComponent(token)}`;

// Public: the token is the secret. A used, expired or withdrawn invitation reads as null.
export const load: PageServerLoad = async ({ params }) => {
	const [invite, config] = await Promise.all([
		flock(invitePath(params.token))
			.then((r) => r.json() as Promise<InviteDetails>)
			.catch((e) => {
				if (isHttpError(e) && e.status === 404) return null;
				throw e;
			}),
		authConfig()
	]);
	return { invite, google: config.google };
};

export const actions: Actions = {
	/** A new account, from the invitation: signs in and opens the org. */
	register: async ({ cookies, params, request }) => {
		const form = await request.formData();
		const name = formText(form, 'name');
		const password = String(form.get('password') ?? '');
		if (!name) return fail(400, { name, error: 'Enter your name' });
		if (password.length < 8) return fail(400, { name, error: 'Use at least 8 characters' });
		const org = formText(form, 'org');
		const res = await signIn(
			cookies,
			`${invitePath(params.token)}/accept`,
			{ name, password },
			entityHref(org)
		);
		return fail(res.status, { name, ...res.data });
	},

	/** Join with the signed-in account (the invited email's). */
	join: async ({ locals, params, request }) => {
		if (!locals.user) return fail(401, { error: 'Sign in first' });
		const org = formText(await request.formData(), 'org');
		const res = await change('POST', `${invitePath(params.token)}/accept`, {});
		if (refused(res)) return res;
		await refreshUser();
		redirect(303, entityHref(org));
	}
};
