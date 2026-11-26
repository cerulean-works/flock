import { error, isHttpError, redirect } from '@sveltejs/kit';
import { loadEditor } from '#lib/components/groups/editor.ts';
import { readPrefill } from '#lib/groups.ts';
import { readFilters } from '#lib/workspace/filters.ts';
import { loginHref } from '#lib/workspace/url.ts';
import type { PageLoad } from './$types';

/**
 * The create page (D223): every run of the project, and what the URL brings along (a
 * suggestion's runs and filters, an ungrouped run, a name). Outsiders can't create groups (D226).
 */
export const load: PageLoad = async ({ fetch, params, url, parent }) => {
	const { shell, user, groups } = await parent();
	if (shell.access === 'public') {
		if (!user) redirect(307, loginHref(url));
		error(404, 'Not found');
	}
	const editor = await loadEditor(fetch, params.entity, params.project).catch((err: unknown) => {
		if (isHttpError(err, 401)) redirect(307, loginHref(url));
		throw err;
	});
	return { ...editor, prefill: readPrefill(url.searchParams, readFilters), groupList: groups };
};
