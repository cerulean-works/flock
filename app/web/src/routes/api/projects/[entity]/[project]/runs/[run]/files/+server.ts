import { json } from '@sveltejs/kit';
import { projectPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** The files a run uploaded. */
export const GET: RequestHandler = async ({ params }) => {
	const res = await flock(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/files`
	);
	return json(await res.json(), { headers: { 'cache-control': 'no-store' } });
};
