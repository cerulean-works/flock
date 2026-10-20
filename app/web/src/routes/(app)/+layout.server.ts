import { redirect } from '@sveltejs/kit';
import { flock } from '#lib/server/flock.ts';
import type { OrgEntry, OrgInfo, ProjectInfo, UserInfo } from '#lib/types.ts';
import { NAME_W, readWidth, SIDEBAR_W } from '#lib/widths.ts';
import { cookieView, VIEW_COOKIE } from '#lib/workspace/project-view.svelte.ts';
import type { LayoutServerLoad } from './$types';

async function listProjects(org: string) {
	const res = await flock(`/api/v1/projects?${new URLSearchParams({ entity: org })}`);
	const { projects } = (await res.json()) as { projects: ProjectInfo[] };
	return projects.toSorted((a, b) => b.updated_at - a.updated_at);
}

/**
 * The sidebar: the organizations to switch between, and the projects of the one in view (the
 * page's own, else the user's default). Reads only the org params, so navigating within an org
 * doesn't re-run it; changes that touch it (a new project, a switch) invalidate everything.
 */
export const load: LayoutServerLoad = async ({ params, cookies }) => {
	const known = params.entity ?? params.org;
	// `/orgs` for the organizations' pictures (`/me` names them only).
	const [me, listed, listedOrgs] = await Promise.all([
		flock('/api/v1/me').then((res) => res.json() as Promise<UserInfo>),
		known ? listProjects(known) : null,
		flock('/api/v1/orgs')
			.then((res) => res.json() as Promise<{ orgs: OrgInfo[] }>)
			.then(({ orgs }) => new Map(orgs.map((o) => [o.name, o])))
	]);
	// A super admin sees every organization: with none, the server's setup is halfway (the
	// account was made, its first workspace not yet), and resumes.
	if (me.is_superadmin && listedOrgs.size === 0) redirect(303, '/setup');
	const org = known ?? me.default_org;
	const projects = listed ?? (org ? await listProjects(org) : []);

	// A super admin can open an org they aren't in: it's listed too while they're in it.
	const visiting = org && me.is_superadmin && !me.orgs.some((o) => o.name === org);
	const joined = visiting
		? [...me.orgs, { name: org, display_name: org, role: 'admin' as const }]
		: me.orgs;
	const orgs: OrgEntry[] = joined.map((o) => {
		const info = listedOrgs.get(o.name);
		return {
			...o,
			display_name: info?.display_name ?? o.display_name,
			avatar_url: info?.avatar_url ?? null
		};
	});
	const current = orgs.find((o) => o.name === org);
	const role = me.is_superadmin ? 'admin' : current?.role;
	return {
		shell: {
			/** Who the pages act as: the signed-in user. */
			me: {
				id: me.id,
				name: me.name,
				email: me.email,
				is_superadmin: me.is_superadmin,
				avatar_url: me.avatar_url
			},
			/** The organization in view: its ID (in URLs), and what people see it called. */
			org,
			orgName: current?.display_name ?? org,
			/** The caller's role in it; super admins are admins everywhere. */
			admin: role === 'admin',
			orgs: orgs.toSorted(
				(a, b) => a.display_name.localeCompare(b.display_name) || a.name.localeCompare(b.name)
			),
			projects,
			/** Kept in a cookie, so the first paint already has the sidebar open or closed. */
			collapsed: cookies.get('flock_sidebar') === 'collapsed',
			/** Dragged widths, in cookies for the same reason: the sidebar's and the name column's. */
			sidebarW: readWidth(SIDEBAR_W, cookies.get(SIDEBAR_W.cookie)),
			nameW: readWidth(NAME_W, cookies.get(NAME_W.cookie)),
			/** The view projects open in, the one last picked: in a cookie for the same reason. */
			projectView: cookieView(cookies.get(VIEW_COOKIE))
		}
	};
};
