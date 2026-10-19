import { fail, redirect } from '@sveltejs/kit';
import { authConfig, signIn } from '#lib/server/auth.ts';
import { formText } from '#lib/utils.ts';
import type { Actions, PageServerLoad } from './$types';

// Only a fresh server is set up: once the first account exists, people sign in (or join from
// an invitation).
export const load: PageServerLoad = async () => {
	const config = await authConfig();
	if (!config.signup_open) redirect(303, '/login');
	return { google: config.google };
};

export const actions: Actions = {
	default: async ({ cookies, request }) => {
		const form = await request.formData();
		const name = formText(form, 'name');
		const email = formText(form, 'email');
		const password = String(form.get('password') ?? '');
		const kept = { name, email };
		if (!name) return fail(400, { ...kept, error: 'Enter your name' });
		if (password.length < 8) return fail(400, { ...kept, error: 'Use at least 8 characters' });
		const res = await signIn(cookies, '/api/v1/auth/signup', { name, email, password });
		return fail(res.status, { ...kept, ...res.data });
	}
};
