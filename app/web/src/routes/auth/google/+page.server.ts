import { redirect } from '@sveltejs/kit';
import { signIn } from '#lib/server/auth.ts';
import type { PageServerLoad } from './$types';

// Where a Google sign-in lands: Flock hands over a one-time code, exchanged here for a
// session. Nothing renders; a failed exchange goes back to sign in.
export const load: PageServerLoad = async ({ cookies, url }) => {
	const code = url.searchParams.get('code') ?? '';
	const res = await signIn(cookies, '/api/v1/auth/google/exchange', { code });
	redirect(303, `/login?${new URLSearchParams({ error: 'expired', message: res.data.error })}`);
};
