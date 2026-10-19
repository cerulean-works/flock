import { fail, isHttpError } from '@sveltejs/kit';
import { signIn } from '#lib/server/auth.ts';
import { flock } from '#lib/server/flock.ts';
import type { Actions, PageServerLoad } from './$types';

const resetPath = (token: string) => `/api/v1/password-resets/${encodeURIComponent(token)}`;

// Public: the token is the secret. A used or expired link reads as null.
export const load: PageServerLoad = async ({ params }) => {
	const reset = await flock(resetPath(params.token))
		.then((r) => r.json() as Promise<{ email: string }>)
		.catch((e) => {
			if (isHttpError(e) && e.status === 404) return null;
			throw e;
		});
	return { email: reset?.email ?? null };
};

export const actions: Actions = {
	/** Set the password and sign in; the account's other sign-ins end. */
	default: async ({ cookies, params, request }) => {
		const password = String((await request.formData()).get('password') ?? '');
		if (password.length < 8) return fail(400, { error: 'Use at least 8 characters' });
		const res = await signIn(cookies, resetPath(params.token), { password });
		return fail(res.status, res.data);
	}
};
