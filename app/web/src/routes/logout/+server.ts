import { redirect } from '@sveltejs/kit';
import { change } from '#lib/server/flock.ts';
import { endSession, safeNext } from '#lib/server/session.ts';
import type { RequestHandler } from './$types';

/**
 * Sign out: end the session at Flock (its tokens stop working at once), then here. Goes
 * to sign in, or to `?next=` (an invitation for another account).
 */
export const POST: RequestHandler = async ({ cookies, locals, url }) => {
	if (locals.user) await change('POST', '/api/v1/auth/logout');
	endSession(cookies);
	const next = url.searchParams.get('next');
	redirect(303, next ? safeNext(next) : '/login');
};
