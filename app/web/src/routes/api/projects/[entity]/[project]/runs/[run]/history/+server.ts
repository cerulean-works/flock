import { projectPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

const PASSED = ['keys', 'stream', 'x', 'max_points'];

/** A run's curves (Home's tiles): a few metrics, a few points each. */
export const GET: RequestHandler = async ({ params, url }) => {
	const search = new URLSearchParams();
	for (const key of PASSED) {
		const value = url.searchParams.get(key);
		if (value) search.set(key, value);
	}
	const res = await flock(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/history?${search}`
	);
	return new Response(res.body, {
		status: res.status,
		headers: { 'content-type': 'application/json' }
	});
};
