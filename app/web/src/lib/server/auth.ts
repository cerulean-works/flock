import { error, fail, isHttpError, redirect, type Cookies } from '@sveltejs/kit';
import { FLOCK_API_URL } from '$app/env/private';
import { getRequestEvent } from '$app/server';
import type { OrgInfo, UserInfo } from '#lib/types.ts';
import { change, refused, flock } from './flock.ts';
import { setUser, startSession, type SessionUser, type SignedIn } from './session.ts';

/** What the sign-in pages offer: `signup_open` until the first account (the super admin) exists. */
export async function authConfig() {
	const res = await flock('/api/v1/auth/config');
	try {
		return (await res.json()) as { signup_open: boolean; google: boolean };
	} catch {
		// Something else answers at FLOCK_API_URL (a web page, a proxy's error).
		error(502, `Flock at ${FLOCK_API_URL} answered something other than its API`);
	}
}

/** Whether Flock has no account yet, so its first visitor sets it up (false if it can't say). */
export async function signupOpen() {
	try {
		return (await authConfig()).signup_open;
	} catch (e) {
		if (isHttpError(e)) return false;
		throw e;
	}
}

/**
 * Sign in through one of Flock's endpoints that start a session (login, signup, the
 * Google code exchange), then go to `next`. A refusal comes back for the form to show, Flock
 * failing too: that is no reason to swap the form for an error page.
 */
export async function signIn(cookies: Cookies, path: string, body: unknown, next = '/') {
	const res = await change<SignedIn>('POST', path, body);
	if (refused(res)) {
		if (res.status < 500 || res.status === 502) return res; // 502: Flock is unreachable
		console.error(`flock answered ${res.status} to ${path}: ${res.data.error}`);
		return fail(502, {
			error: `Flock couldn’t sign you in (it answered ${res.status}): try again`
		});
	}
	startSession(cookies, res.data);
	redirect(303, next);
}

/**
 * Whether the caller is a super admin on a server without any organization: Flock's setup is
 * then halfway, with the first workspace still to create.
 */
export async function needsWorkspace() {
	const me = (await (await flock('/api/v1/me')).json()) as UserInfo;
	if (!me.is_superadmin) return false;
	// Every organization, for a super admin.
	const { orgs } = (await (await flock('/api/v1/orgs')).json()) as { orgs: OrgInfo[] };
	return orgs.length === 0;
}

/** Re-read the signed-in user's profile once a change touched it (an org joined, created or
 * left; super admin given up). */
export async function refreshUser() {
	const { cookies, locals } = getRequestEvent();
	if (!locals.user) return;
	const res = await flock('/api/v1/me');
	setUser(cookies, (await res.json()) as SessionUser);
}
