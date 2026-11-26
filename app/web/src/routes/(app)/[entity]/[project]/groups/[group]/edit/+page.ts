import { error, isHttpError, redirect } from '@sveltejs/kit';
import { loadEditor } from '#lib/components/groups/editor.ts';
import { groupsApi, loadCall, type GroupDetail } from '#lib/groups.ts';
import { loginHref } from '#lib/workspace/url.ts';
import type { PageLoad } from './$types';

/**
 * A group's edit page (D223): every run of the project with the group's ticked, and with
 * `?new=1`, its new matches first (D216). Outsiders can't edit groups (D226).
 */
export const load: PageLoad = async ({ fetch, params, url, parent }) => {
	const id = Number(params.group);
	if (!Number.isInteger(id) || id <= 0) error(404, 'Not found');
	const { shell, user, groups } = await parent();
	if (shell.access === 'public') {
		if (!user) redirect(307, loginHref(url));
		error(404, 'Not found');
	}
	const [group, editor] = await Promise.all([
		loadCall<GroupDetail>(fetch, groupsApi(params.entity, params.project, String(id))),
		loadEditor(fetch, params.entity, params.project)
	]).catch((err: unknown) => {
		if (isHttpError(err, 401)) redirect(307, loginHref(url));
		throw err;
	});
	return { ...editor, group, fresh: url.searchParams.get('new') === '1', groupList: groups };
};
