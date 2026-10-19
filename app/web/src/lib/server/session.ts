import type { Cookies } from '@sveltejs/kit';
import { FLOCK_URL } from '$app/env/private';

/**
 * Sign-in sessions, kept by this server.
 *
 * Flock issues a short-lived access token (a JWT, 15 minutes) and a refresh token that
 * works once. Both live in httpOnly cookies, so page scripts never see them; requests to the
 * flock carry the access token, and an expired one is refreshed here before the request
 * goes on. The signed-in user's profile is kept in a third cookie, for the account menu.
 */

/** The signed-in user, as shown in the account menu. */
export interface SessionUser {
	id: number;
	email: string;
	name: string;
	is_superadmin: boolean;
	default_org: string | null;
}

interface Tokens {
	access_token: string;
	expires_in: number;
	refresh_token: string;
	/** Seconds since the epoch. */
	refresh_expires_at: number;
}

/** What Flock answers a sign-in with (signup, login, Google, a password reset). */
export interface SignedIn extends Tokens {
	user: SessionUser;
}

const ACCESS = 'flock_access';
const REFRESH = 'flock_refresh';
const USER = 'flock_user';
const OPTIONS = { path: '/', httpOnly: true, sameSite: 'lax' } as const;

/** Refresh this long before the access token expires, so it doesn't lapse mid-request. */
const MARGIN = 30;

/** The profile is read only alongside a token, so it can outlive the session it belongs to. */
const USER_MAX_AGE = 400 * 24 * 3600;

function setTokens(cookies: Cookies, tokens: Tokens) {
	const refreshFor = Math.floor(tokens.refresh_expires_at - Date.now() / 1000);
	cookies.set(ACCESS, tokens.access_token, { ...OPTIONS, maxAge: tokens.expires_in - MARGIN });
	cookies.set(REFRESH, tokens.refresh_token, { ...OPTIONS, maxAge: refreshFor });
}

/** Keep the signed-in user's profile, e.g. after they join an org or stop being a super admin. */
export function setUser(cookies: Cookies, profile: SessionUser) {
	const { id, email, name, is_superadmin, default_org } = profile;
	const user: SessionUser = { id, email, name, is_superadmin, default_org };
	cookies.set(USER, Buffer.from(JSON.stringify(user)).toString('base64url'), {
		...OPTIONS,
		maxAge: USER_MAX_AGE
	});
}

export function startSession(cookies: Cookies, signedIn: SignedIn) {
	setTokens(cookies, signedIn);
	setUser(cookies, signedIn.user);
}

export function endSession(cookies: Cookies) {
	for (const name of [ACCESS, REFRESH, USER]) cookies.delete(name, { path: '/' });
}

function readUser(cookies: Cookies): SessionUser | null {
	const raw = cookies.get(USER);
	if (!raw) return null;
	try {
		return JSON.parse(Buffer.from(raw, 'base64url').toString()) as SessionUser;
	} catch {
		return null;
	}
}

/**
 * A refresh token works once, and a page arrives with its data requests at the same moment:
 * requests carrying the same refresh token share one refresh, and its result for a minute.
 */
const refreshing = new Map<string, Promise<Tokens | null | 'unreachable'>>();

function refresh(token: string) {
	let pending = refreshing.get(token);
	if (!pending) {
		pending = fetch(`${FLOCK_URL}/api/v1/auth/refresh`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ refresh_token: token })
		})
			.then((res) => (res.ok ? (res.json() as Promise<Tokens>) : null))
			.catch(() => 'unreachable' as const);
		refreshing.set(token, pending);
		setTimeout(() => refreshing.delete(token), 60_000);
	}
	return pending;
}

/** The request's access token, refreshed if it has expired; null when nobody is signed in. */
export async function resolveSession(
	cookies: Cookies
): Promise<{ token: string | null; user: SessionUser | null }> {
	let token = cookies.get(ACCESS) ?? null;
	const refreshToken = cookies.get(REFRESH);
	if (!token && refreshToken) {
		const tokens = await refresh(refreshToken);
		if (tokens === 'unreachable') return { token: null, user: readUser(cookies) };
		if (tokens) {
			setTokens(cookies, tokens);
			token = tokens.access_token;
		} else {
			endSession(cookies); // ended elsewhere: signed out, a password change, ...
		}
	}
	return { token, user: token ? readUser(cookies) : null };
}

/** Where to sign in, coming back to `url` afterwards. */
export function loginHref(url: URL) {
	const next = url.pathname + url.search;
	return next === '/' ? '/login' : `/login?${new URLSearchParams({ next })}`;
}

/** A `next` parameter that is safe to redirect to: a path on this site. */
export function safeNext(next: string | null) {
	return next?.startsWith('/') && !next.startsWith('//') ? next : '/';
}
