import { isHttpError, redirect } from '@sveltejs/kit';
import {
	GROUPS,
	loadCall,
	newGroupHref,
	runsQueryApi,
	groupsApi,
	type Comparison,
	type RunsResult
} from '#lib/groups.ts';
import { loginHref } from '#lib/workspace/url.ts';
import type { PageLoad } from './$types';

/**
 * Compare (D188): every group's best, side by side, and the ungrouped runs (D187). The groups
 * themselves come from the project's layout; their suggested columns are read by the page,
 * without holding it up. The project's summary columns are what
 * **Best by** offers: a runs query of no runs still names the columns a pattern matches.
 *
 * A project without groups has nothing to compare yet: Groups opens on creating them instead
 * (the create page, which proposes groups from the runs). Viewers who can't create any stay.
 */
export const load: PageLoad = async ({ fetch, params, url, parent, depends }) => {
	depends(GROUPS);
	const { groups, shell } = await parent();
	if (groups && !groups.groups.length && shell.access !== 'public')
		redirect(307, newGroupHref(params.entity, params.project));
	const [compare, summary] = await Promise.all([
		loadCall<Comparison>(fetch, groupsApi(params.entity, params.project, 'compare')).catch(
			async (err: unknown) => {
				// Signed out, a missing project may be a private one (D116): sign in, then come back.
				if (isHttpError(err, 401) || (isHttpError(err, 404) && !(await parent()).user))
					redirect(307, loginHref(url));
				throw err;
			}
		),
		loadCall<RunsResult>(fetch, runsQueryApi(params.entity, params.project), {
			method: 'POST',
			body: { columns: ['summary.*'], limit: 0 }
		}).catch(() => null)
	]);
	return { compare, summaryKeys: summary?.columns ?? [] };
};
