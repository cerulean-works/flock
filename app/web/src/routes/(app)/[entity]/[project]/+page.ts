import { isHttpError, redirect } from '@sveltejs/kit';
import { loadRunPage } from '#lib/data/cache.ts';
import { applyRunQuery, loginHref, readRunQuery } from '#lib/workspace/url.ts';
import type { PageLoad } from './$types';

// One request per page: Flock snapshot carries the column schema, the runs and their
// curves together, so nothing waits on anything else. Only `page`, `sort`, `order`, `size`, `q`
// and `filters` are read, so SvelteKit re-runs this load for those params alone; view switches and full-screen panels
// (`view`, `panel`) are free navigations.
export const load: PageLoad = async ({ fetch, params, url, parent }) => {
	const query = readRunQuery(url.searchParams);
	const { meta, page } = await loadRunPage(fetch, params.entity, params.project, query).catch(
		async (err: unknown) => {
			// The session ended since the page was opened: sign in, then come back here. Signed
			// out, a missing project may be a private one (D116): the same.
			if (isHttpError(err, 401) || (isHttpError(err, 404) && !(await parent()).user))
				redirect(307, loginHref(url));
			throw err;
		}
	);
	if (!page.runs.length && query.page > 1) {
		// Past the end (e.g. an old link after runs were deleted): go to the last page.
		const search = new URLSearchParams(url.search);
		applyRunQuery(search, { ...query, page: Math.max(1, Math.ceil(page.total / query.size)) });
		redirect(307, `${url.pathname}?${search}`);
	}
	return { meta, runs: page };
};
