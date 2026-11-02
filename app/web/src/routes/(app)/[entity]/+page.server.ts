import { fail, redirect } from '@sveltejs/kit';
import { change, projectPath, refused, flock } from '#lib/server/flock.ts';
import { FEED } from '#lib/data/feed.svelte.ts';
import type { FeedPage, OrgInfo, ProjectInfo } from '#lib/types.ts';
import { formText, projectNameError } from '#lib/utils.ts';
import { entityHref, projectHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * An organization's latest runs, newest first: the feed's `run_started` items. `?runs=mine`
 * keeps them to the user's own, `?project=` to one project.
 *
 * Outside the org (signed out, or not one of its members), its public projects instead (D114):
 * the sidebar's, which lists only public ones to outsiders.
 */
export const load: PageServerLoad = async ({ params, url, depends, locals }) => {
	depends(FEED);
	const now = Date.now() / 1000;
	const orgPath = `/api/v1/orgs/${encodeURIComponent(params.entity)}`;
	const outside = (org: OrgInfo) => ({
		org,
		outside: true as const,
		feed: null,
		query: {},
		mine: false,
		project: null,
		now
	});
	// Signed out there's no feed to read: the org is someone else's. (A 404, an org with
	// no public project, sends the visitor to sign in.)
	if (!locals.token) return outside(await flock(orgPath).then((r) => r.json() as Promise<OrgInfo>));

	const mine = url.searchParams.get('runs') === 'mine';
	const project = url.searchParams.get('project') || null;
	const query: Record<string, string> = {
		org: params.entity,
		kinds: 'run_started',
		limit: '50',
		...(mine && { owner: 'me' }),
		...(project && { project })
	};
	// The org answers 404 to non-members unless it has public projects, and says they have no
	// role. The feed is members' only, a 404 to anyone else: it's read alongside, and its
	// failure only matters once the org says the viewer is a member.
	const feeding = flock(`/api/v1/feed?${new URLSearchParams(query)}`).then(
		(r) => r.json() as Promise<FeedPage>
	);
	feeding.catch(() => {});
	const org = await flock(orgPath).then((r) => r.json() as Promise<OrgInfo>);
	if (!org.role) return outside(org);
	const feed = await feeding;
	// Relative times are computed against this, so SSR and hydration print the same text.
	return { org, outside: false as const, feed, query, mine, project, now };
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
		// Only admins' forms have it (D110): making the project public or private.
		const visibility = formText(form, 'visibility');
		const res = await change<ProjectInfo>('PATCH', projectPath(params.entity, project), {
			...(name !== project && { name }),
			description: formText(form, 'description') || null,
			...((visibility === 'private' || visibility === 'public') && { visibility })
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
