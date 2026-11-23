import { error, isHttpError, redirect } from '@sveltejs/kit';
import { loadRunPage } from '#lib/data/cache.ts';
import { GROUPS, groupsApi, loadCall, type GroupColumn, type GroupDetail } from '#lib/groups.ts';
import { loginHref, readRunQuery } from '#lib/workspace/url.ts';
import type { PageLoad } from './$types';

/**
 * A group's page (D189): the group, a page of its runs as the Runs page reads them (the
 * workspace snapshot, searched, filtered, sorted and paged by the URL), and each config key's
 * kind (D185). Its primary columns are streamed, not awaited: they may take a model's turn,
 * and the table shows the Varies columns until they come. Read again when a group changes or
 * live events say values did (D195).
 */
export const load: PageLoad = async ({ fetch, params, url, parent, depends }) => {
	depends(GROUPS);
	const id = Number(params.group);
	if (!Number.isInteger(id) || id <= 0) error(404, 'Not found');
	const op = String(id);
	const signIn = async (err: unknown) => {
		// Signed out, a missing project may be a private one (D116): sign in, then come back.
		if (isHttpError(err, 401) || (isHttpError(err, 404) && !(await parent()).user))
			redirect(307, loginHref(url));
		throw err;
	};
	const primary = loadCall<{ keys: string[] }>(
		fetch,
		groupsApi(params.entity, params.project, `${op}/primary`)
	)
		.then((r) => (Array.isArray(r.keys) ? r.keys.filter((k) => typeof k === 'string') : null))
		.catch(() => null);
	const [group, { meta, page }, columns] = await Promise.all([
		loadCall<GroupDetail>(fetch, groupsApi(params.entity, params.project, op)).catch(signIn),
		loadRunPage(fetch, params.entity, params.project, readRunQuery(url.searchParams), id).catch(
			signIn
		),
		loadCall<{ columns: GroupColumn[] }>(
			fetch,
			groupsApi(params.entity, params.project, `${op}/columns`)
		)
			.then((r) => r.columns)
			.catch(() => null)
	]);
	return { group, meta, runs: page, columns, primary };
};
