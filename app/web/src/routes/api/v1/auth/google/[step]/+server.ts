import { error } from '@sveltejs/kit';
import { FLOCK_URL } from '$app/env/private';
import type { RequestHandler } from './$types';

/**
 * Flock's Google sign-in (`start`, and the `callback` Google returns to), on this site.
 * Browsers then needn't reach Flock, and the flow's cookie (scoped to this path) is set
 * on the dashboard's origin. Flock's redirects are passed back as they are, except that
 * a link to Flock's own address points here instead.
 */
export const GET: RequestHandler = async ({ params, url, request }) => {
	if (params.step !== 'start' && params.step !== 'callback') error(404, 'Not found');
	const res = await fetch(`${FLOCK_URL}/api/v1/auth/google/${params.step}${url.search}`, {
		headers: { cookie: request.headers.get('cookie') ?? '' },
		redirect: 'manual'
	}).catch(() => error(502, 'Flock is not reachable'));
	const headers = new Headers({ 'cache-control': 'no-store' });
	const location = res.headers.get('location');
	if (location) headers.set('location', location.replace(FLOCK_URL, url.origin));
	for (const cookie of res.headers.getSetCookie()) headers.append('set-cookie', cookie);
	if (res.status >= 400) {
		const body = (await res.json().catch(() => null)) as { error?: string } | null;
		error(res.status as 404, body?.error ?? 'Google sign-in failed');
	}
	return new Response(null, { status: res.status, headers });
};
