import { projectPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/**
 * Who logged a project's runs, without their emails for viewers outside the org: what its
 * `owner` column names by id when the org's members can't be listed (D113).
 */
export const GET: RequestHandler = async ({ params }) => {
	const res = await flock(`${projectPath(params.entity, params.project)}/owners`);
	return new Response(res.body, { headers: { 'content-type': 'application/json' } });
};
