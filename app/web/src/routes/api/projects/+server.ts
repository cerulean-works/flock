import { flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** Every project with its run counts, or only an entity's with `?entity=`. */
export const GET: RequestHandler = async ({ url }) => {
	const entity = url.searchParams.get('entity');
	const search = entity ? `?${new URLSearchParams({ entity })}` : '';
	const res = await flock(`/api/v1/projects${search}`);
	return new Response(res.body, { headers: { 'content-type': 'application/json' } });
};
