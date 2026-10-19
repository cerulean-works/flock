import { projectPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** One project's description and run counts. */
export const GET: RequestHandler = async ({ params }) => {
	const res = await flock(projectPath(params.entity, params.project));
	return new Response(res.body, { headers: { 'content-type': 'application/json' } });
};
