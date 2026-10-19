import { readRunQuery, flockSort } from '#lib/workspace/url.ts';
import { projectPath, flock } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/**
 * One page of runs as Flock's binary workspace snapshot. `enc=b64` returns it as base64
 * text; that variant is only requested during SSR, where SvelteKit inlines the response into
 * the HTML (see #lib/data/cache.ts).
 */
export const GET: RequestHandler = async ({ params, url }) => {
	const q = readRunQuery(url.searchParams);
	const search = new URLSearchParams({
		sort: flockSort(q),
		offset: String((q.page - 1) * q.size),
		limit: String(q.size),
		system: 'true'
	});
	const res = await flock(`${projectPath(params.entity, params.project)}/workspace?${search}`);
	const headers = { 'cache-control': 'no-store' };
	if (url.searchParams.get('enc') === 'b64') {
		const body = Buffer.from(await res.arrayBuffer()).toString('base64');
		return new Response(body, { headers: { ...headers, 'content-type': 'text/plain' } });
	}
	return new Response(res.body, {
		headers: { ...headers, 'content-type': 'application/octet-stream' }
	});
};
