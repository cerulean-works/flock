import { json } from '@sveltejs/kit';
import { projectPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** A page of a run's console output: `offset` and `limit` lines. */
export const GET: RequestHandler = async ({ params, url }) => {
	const search = new URLSearchParams({
		offset: String(Math.max(0, Math.floor(Number(url.searchParams.get('offset')) || 0))),
		limit: String(
			Math.min(5000, Math.max(1, Math.floor(Number(url.searchParams.get('limit')) || 1000)))
		)
	});
	const res = await flock(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/logs?${search}`
	);
	return json(await res.json(), { headers: { 'cache-control': 'no-store' } });
};
