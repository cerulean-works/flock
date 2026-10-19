import { redirect, type Cookies } from '@sveltejs/kit';
import { getRequestEvent } from '$app/server';
import { change, refused, flock } from './flock.ts';
import { setUser, startSession, type SessionUser, type SignedIn } from './session.ts';

/** What the sign-in pages offer: `signup_open` until the first account (the super admin) exists. */
export async function authConfig() {
	const res = await flock('/api/v1/auth/config');
	return (await res.json()) as { signup_open: boolean; google: boolean };
}

/**
 * Sign in through one of Flock's endpoints that start a session (login, signup, the
 * Google code exchange), then go to `next`. A refusal comes back for the form to show.
 */
export async function signIn(cookies: Cookies, path: string, body: unknown, next = '/') {
	const res = await change<SignedIn>('POST', path, body);
	if (refused(res)) return res;
	startSession(cookies, res.data);
	redirect(303, next);
}

/** Re-read the signed-in user's profile once a change touched it (an org joined, renamed or
 * left; super admin given up). */
export async function refreshUser() {
	const { cookies, locals } = getRequestEvent();
	if (!locals.user) return;
	const res = await flock('/api/v1/me');
	setUser(cookies, (await res.json()) as SessionUser);
}
