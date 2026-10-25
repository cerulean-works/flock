import { json } from '@sveltejs/kit';
import { datasetPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

const int = (raw: string | null, fallback: number, min: number, max: number) =>
	Math.min(max, Math.max(min, Math.floor(Number(raw ?? fallback)) || fallback));

/**
 * A page of a dataset's rows, for the viewer: `offset` and `limit` (at most 500), at `version`
 * (the latest without it), optionally only some `columns` (a comma list).
 */
export const GET: RequestHandler = async ({ params, url }) => {
	const q = url.searchParams;
	const search = new URLSearchParams({
		offset: String(int(q.get('offset'), 0, 0, Number.MAX_SAFE_INTEGER)),
		limit: String(int(q.get('limit'), 100, 1, 500))
	});
	const version = Number(q.get('version'));
	if (Number.isInteger(version) && version >= 1) search.set('version', String(version));
	const columns = q.get('columns');
	if (columns) search.set('columns', columns);
	const res = await flock(`${datasetPath(params.entity, params.dataset)}/rows?${search}`);
	return json(await res.json(), { headers: { 'cache-control': 'no-store' } });
};
