import { flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

const PASSED = ['org', 'project', 'owner', 'kinds', 'cursor', 'limit'];

/** A page of Flock's activity feed, for "Show more": the same query, plus a cursor. */
export const GET: RequestHandler = async ({ url }) => {
	const search = new URLSearchParams();
	for (const key of PASSED) {
		const value = url.searchParams.get(key);
		if (value) search.set(key, value);
	}
	const res = await flock(`/api/v1/feed?${search}`);
	return new Response(res.body, { headers: { 'content-type': 'application/json' } });
};
