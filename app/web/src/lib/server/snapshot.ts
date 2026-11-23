import { flockFilter } from '#lib/workspace/filters.ts';
import { flockSort, readRunQuery } from '#lib/workspace/url.ts';
import { flock } from './flock.ts';

/**
 * One page of runs as Flock's binary workspace snapshot (`path`: a project's `/workspace`, or a
 * group's), searched, filtered, sorted and paged as the page's URL asks. `enc=b64` returns it as
 * base64 text; that variant is only requested during SSR, where SvelteKit inlines the response
 * into the HTML (see #lib/data/cache.ts).
 */
export async function relaySnapshot(path: string, url: URL): Promise<Response> {
	const q = readRunQuery(url.searchParams);
	const search = new URLSearchParams({
		sort: flockSort(q),
		offset: String((q.page - 1) * q.size),
		limit: String(q.size),
		system: 'true'
	});
	const filter = flockFilter(q.search, q.filters);
	if (filter) search.set('filter', JSON.stringify(filter));
	const res = await flock(`${path}?${search}`);
	const headers = { 'cache-control': 'no-store' };
	if (url.searchParams.get('enc') === 'b64') {
		const body = Buffer.from(await res.arrayBuffer()).toString('base64');
		return new Response(body, { headers: { ...headers, 'content-type': 'text/plain' } });
	}
	return new Response(res.body, {
		headers: { ...headers, 'content-type': 'application/octet-stream' }
	});
}
