import { error } from '@sveltejs/kit';
import { FLOCK_API_URL } from '$app/env/private';
import type { RequestHandler } from './$types';

/** Headers of Flock's answer that go on to the browser. */
const PASSED = [
	'content-type',
	'cache-control',
	'etag',
	'x-content-type-options',
	'content-security-policy'
];

/**
 * Avatars, at the path Flock gives as `avatar_url`. They need no credentials (the digest
 * is the permission), so this forwards nothing but the cache validator. Each URL's bytes never
 * change: browsers keep them a year, and a new picture is a new URL.
 */
export const GET: RequestHandler = async ({ params, request }) => {
	if (!/^[0-9a-f]{64}$/.test(params.digest)) error(404, 'No such avatar');
	const etag = request.headers.get('if-none-match');
	let res: Response;
	try {
		res = await fetch(`${FLOCK_API_URL}/api/v1/avatars/${params.digest}`, {
			headers: etag ? { 'if-none-match': etag } : {}
		});
	} catch {
		error(502, 'Flock is not reachable');
	}
	if (res.status !== 200 && res.status !== 304)
		error(res.status === 404 ? 404 : 502, 'No such avatar');
	const headers = new Headers();
	for (const name of PASSED) {
		const value = res.headers.get(name);
		if (value) headers.set(name, value);
	}
	return new Response(res.status === 304 ? null : res.body, { status: res.status, headers });
};
