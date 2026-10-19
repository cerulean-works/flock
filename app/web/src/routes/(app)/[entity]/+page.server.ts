import { fail, redirect } from '@sveltejs/kit';
import { change, projectPath, refused, flock } from '#lib/server/flock.ts';
import { FEED } from '#lib/data/feed.svelte.ts';
import type { FeedPage, OrgInfo, ProjectInfo } from '#lib/types.ts';
import { formText, projectNameError } from '#lib/utils.ts';
import { entityHref, projectHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * An organization's latest runs, newest first: the feed's `run_started` items. `?runs=mine`
 * keeps them to the user's own.
 */
export const load: PageServerLoad = async ({ params, url, depends }) => {
	depends(FEED);
	const mine = url.searchParams.get('runs') === 'mine';
	const query: Record<string, string> = {
		org: params.entity,
		kinds: 'run_started',
		limit: '50',
		...(mine && { owner: 'me' })
	};
	// The org answers 404 to non-members (the feed would just be empty).
	const [org, feed] = await Promise.all([
		flock(`/api/v1/orgs/${encodeURIComponent(params.entity)}`).then(
			(r) => r.json() as Promise<OrgInfo>
		),
		flock(`/api/v1/feed?${new URLSearchParams(query)}`).then((r) => r.json() as Promise<FeedPage>)
	]);
	// Relative times are computed against this, so SSR and hydration print the same text.
	return { org, feed, query, mine, now: Date.now() / 1000 };
};

/**
 * Project changes. The workspace posts here too (`/{entity}?/update`); it sends `from=project`
 * so that a rename or delete lands on the right page afterwards.
 */
export const actions: Actions = {
	create: async ({ params, request }) => {
		const form = await request.formData();
		const name = formText(form, 'name');
		const invalid = projectNameError(name);
		if (invalid) return fail(400, { error: invalid });
		const res = await change('POST', '/api/v1/projects', {
			entity: params.entity,
			name,
			description: formText(form, 'description') || null
		});
		if (refused(res)) return res;
		redirect(303, projectHref(params.entity, name));
	},

	update: async ({ params, request }) => {
		const form = await request.formData();
		const project = formText(form, 'project');
		const name = formText(form, 'name');
		const invalid = projectNameError(name);
		if (invalid) return fail(400, { error: invalid });
		const res = await change<ProjectInfo>('PATCH', projectPath(params.entity, project), {
			...(name !== project && { name }),
			description: formText(form, 'description') || null
		});
		if (refused(res)) return res;
		if (form.get('from') === 'project' && name !== project)
			redirect(303, projectHref(params.entity, name));
		return { project: res.data };
	},

	delete: async ({ params, request }) => {
		const form = await request.formData();
		const project = formText(form, 'project');
		const force = form.get('force') === 'on' ? '?force=true' : '';
		const res = await change('DELETE', `${projectPath(params.entity, project)}${force}`);
		if (refused(res)) return res;
		if (form.get('from') === 'project') redirect(303, entityHref(params.entity));
		return { deleted: project };
	}
};
