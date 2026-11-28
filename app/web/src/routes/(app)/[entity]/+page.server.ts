import { fail, redirect } from '@sveltejs/kit';
import { change, projectPath, refused, flock } from '#lib/server/flock.ts';
import type { Credits } from '#lib/home/credits.ts';
import { creditsConfigured, creditsSummary } from '#lib/server/credits.ts';
import { FEED, RUN_FILTERS, runStateKinds, type RunFilters } from '#lib/data/feed.svelte.ts';
import type { Budget, FeedPage, Member, OrgInfo, ProjectInfo, RunFacets } from '#lib/types.ts';
import { formText, monthOf, projectNameError } from '#lib/utils.ts';
import { entityHref, projectHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * An organization's runs, newest first: the feed's `run_started` items, one row a run. The way
 * in (Home was folded in here), so its budget comes too. `?runs=mine` keeps them to the user's
 * own, `?project=` to one project, `?state=finished|failed` to the runs that ended so;
 * `?month=YYYY-MM` shows an earlier month's budget.
 *
 * Outside the org (signed out, or not one of its members), its public projects instead (D114):
 * the sidebar's, which lists only public ones to outsiders.
 */
export const load: PageServerLoad = async ({ params, url, depends, locals }) => {
	depends(FEED);
	const now = Date.now() / 1000;
	const orgPath = `/api/v1/orgs/${encodeURIComponent(params.entity)}`;
	const list = (key: string) =>
		(url.searchParams.get(key) ?? '')
			.split(',')
			.map((v) => v.trim())
			.filter(Boolean);
	const filters = Object.fromEntries(RUN_FILTERS.map((k) => [k, list(k)])) as RunFilters;
	// Home's old `?runs=mine` link: the viewer's own runs.
	if (url.searchParams.get('runs') === 'mine' && !filters.owner.includes('me'))
		filters.owner.push('me');
	filters.owner = filters.owner.filter((v) => v === 'me' || /^\d+$/.test(v));
	filters.state = filters.state.filter((v) => runStateKinds(v));
	const outside = (org: OrgInfo) => ({
		org,
		outside: true as const,
		feed: null,
		query: {},
		filters,
		mine: false,
		members: [] as Pick<Member, 'id' | 'name' | 'avatar_url'>[],
		facets: null as RunFacets | null,
		budget: null,
		credits: null as Promise<Credits | null> | null,
		thisMonth: monthOf(now),
		now
	});
	// Signed out there's no feed to read: the org is someone else's. (A 404, an org with
	// no public project, sends the visitor to sign in.)
	if (!locals.token) return outside(await flock(orgPath).then((r) => r.json() as Promise<OrgInfo>));

	// The states chosen as the feed kinds that say them; else every run, once, by its start.
	const kinds = filters.state.flatMap((v) => runStateKinds(v)!.kinds.split(','));
	const query: Record<string, string> = {
		org: params.entity,
		kinds: kinds.length ? kinds.join(',') : 'run_started',
		limit: '50'
	};
	for (const key of ['owner', 'project', 'group', 'job_type', 'gpu'] as const)
		if (filters[key].length) query[key] = filters[key].join(',');
	// The org answers 404 to non-members unless it has public projects, and says they have no
	// role. The feed is members' only, a 404 to anyone else: it's read alongside, and its
	// failure only matters once the org says the viewer is a member.
	const read = <T>(path: string) => {
		const p = flock(path).then((r) => r.json() as Promise<T>);
		p.catch(() => {});
		return p;
	};
	const feeding = read<FeedPage>(`/api/v1/feed?${new URLSearchParams(query)}`);
	const month = url.searchParams.get('month');
	// The credit dashboard, when Flock is told where it is, stands in for Flock's own budget.
	const walleted = creditsConfigured();
	const budgeting = walleted
		? Promise.resolve(null)
		: read<Budget>(
				`${orgPath}/budget${month && /^\d{4}-\d{2}$/.test(month) ? `?month=${month}` : ''}`
			);
	// Streamed: the page doesn't wait on another service; the panel fills in when it answers.
	const crediting = walleted ? creditsSummary(month, 10_000) : null;
	const membering = read<{ members: Member[] }>(`${orgPath}/members`);
	// What each column can be filtered to, with counts, across all the org's runs.
	const faceting = read<RunFacets>(`${orgPath}/run-facets`);
	const org = await flock(orgPath).then((r) => r.json() as Promise<OrgInfo>);
	if (!org.role) return outside(org);
	const [feed, budget, { members }, facets] = await Promise.all([
		feeding,
		budgeting,
		membering,
		faceting
	]);
	// Relative times are computed against this, so SSR and hydration print the same text.
	return {
		org,
		outside: false as const,
		feed,
		query,
		filters,
		mine: filters.owner.length === 1 && filters.owner[0] === 'me',
		members: members.map(({ id, name, avatar_url }) => ({ id, name, avatar_url })),
		facets,
		budget,
		credits: crediting,
		thisMonth: monthOf(now),
		now
	};
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
			description: formText(form, 'description') || null,
			// Sent only when public: private is the default.
			...(formText(form, 'visibility') === 'public' && { visibility: 'public' })
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
		// Only the forms of admins and the project's creator have it (D110, D118).
		const visibility = formText(form, 'visibility');
		const res = await change<ProjectInfo>('PATCH', projectPath(params.entity, project), {
			...(name !== project && { name }),
			// Absent from a creator's form, who may change only the visibility (D118).
			...(form.has('description') && { description: formText(form, 'description') || null }),
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
