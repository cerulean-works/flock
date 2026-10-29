import { flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** An organization's members: who logged the runs a project's `owner` column names by id. */
export const GET: RequestHandler = async ({ params }) => {
	const res = await flock(`/api/v1/orgs/${encodeURIComponent(params.org)}/members`);
	return new Response(res.body, { headers: { 'content-type': 'application/json' } });
};
