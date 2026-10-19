import { fail, redirect } from '@sveltejs/kit';
import { authConfig, signIn, signupOpen } from '#lib/server/auth.ts';
import { safeNext } from '#lib/server/session.ts';
import { formText } from '#lib/utils.ts';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url }) => {
	const next = safeNext(url.searchParams.get('next'));
	// A profile without a token is a session Flock couldn't refresh (it was unreachable):
	// sending it on would only come back here.
	if (locals.user && locals.token) redirect(303, next);
	const config = await authConfig();
	// A fresh server has nobody to sign in as: its first visitor sets it up.
	if (config.signup_open) redirect(303, '/setup');
	return { google: config.google };
};

export const actions: Actions = {
	default: async ({ cookies, request, url }) => {
		const form = await request.formData();
		const email = formText(form, 'email');
		const res = await signIn(
			cookies,
			'/api/v1/auth/login',
			{ email, password: String(form.get('password') ?? '') },
			safeNext(url.searchParams.get('next'))
		);
		// A form opened before Flock lost its accounts (a fresh database): there is nobody to
		// sign in as until it is set up.
		if (res.status === 401 && (await signupOpen())) redirect(303, '/setup');
		// Refused: show why, keeping the email typed.
		return fail(res.status, { ...res.data, email });
	}
};
