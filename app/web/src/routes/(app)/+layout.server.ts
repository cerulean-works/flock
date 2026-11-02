import { redirect } from '@sveltejs/kit';
import { authConfig } from '#lib/server/auth.ts';
import { flock, flockAnswer } from '#lib/server/flock.ts';
import type { Access, OrgEntry, OrgInfo, ProjectInfo, UserInfo } from '#lib/types.ts';
import { NAME_W, readWidth, SIDEBAR_W } from '#lib/widths.ts';
import { cookieView, VIEW_COOKIE } from '#lib/workspace/project-view.svelte.ts';
import type { LayoutServerLoad } from './$types';

async function listProjects(org: string) {
	const res = await flock(`/api/v1/projects?${new URLSearchParams({ entity: org })}`);
	const { projects } = (await res.json()) as { projects: ProjectInfo[] };
	return projects.toSorted((a, b) => b.updated_at - a.updated_at);
}

/**
 * An org the viewer isn't in, as it shows itself to outsiders (its name and picture); null when
 * it has nothing public, or doesn't exist (D114).
 */
async function outsideView(org: string) {
	const res = await flockAnswer(`/api/v1/orgs/${encodeURIComponent(org)}`);
	if (!res.ok) {
		await res.body?.cancel();
		return null;
	}
	return (await res.json()) as Pick<OrgInfo, 'name' | 'display_name' | 'avatar_url'>;
}

/**
 * The sidebar: the organizations to switch between, and the projects of the one in view (the
 * page's own, else the user's default). Reads only the org params, so navigating within an org
 * doesn't re-run it; changes that touch it (a new project, a switch) invalidate everything.
 *
 * Signed out, or in an org they aren't in, the viewer sees it from outside (D116): its name and
 * picture, and its public projects (`GET /projects?entity=` lists only those to them).
 */
export const load: LayoutServerLoad = async ({ params, cookies, locals }) => {
	const known = params.entity ?? params.org;
	const signedIn = !!locals.token;
	// `/orgs` for the organizations' pictures (`/me` names them only).
	const [me, listed, listedOrgs, config, seen] = await Promise.all([
		signedIn ? flock('/api/v1/me').then((res) => res.json() as Promise<UserInfo>) : null,
		known ? listProjects(known) : null,
		signedIn
			? flock('/api/v1/orgs')
					.then((res) => res.json() as Promise<{ orgs: OrgInfo[] }>)
					.then(({ orgs }) => new Map(orgs.map((o) => [o.name, o])))
			: new Map<string, OrgInfo>(),
		authConfig(),
		// Signed out, any org is someone else's.
		known && !signedIn ? outsideView(known) : null
	]);
	// A super admin sees every organization: with none, the server's setup is halfway (the
	// account was made, its first workspace not yet), and resumes.
	if (me?.is_superadmin && listedOrgs.size === 0) redirect(303, '/setup');
	const org = known ?? me?.default_org ?? null;
	const projects = listed ?? (org ? await listProjects(org) : []);

	// A super admin can open an org they aren't in: it's listed too while they're in it.
	const visiting = org && me?.is_superadmin && !me.orgs.some((o) => o.name === org);
	const joined = !me
		? []
		: visiting
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
	const access: Access = me?.is_superadmin ? 'admin' : (current?.role ?? 'public');
	// Signed in, but not in this org: how it shows itself to outsiders.
	const outside = org && !current ? (seen ?? (signedIn ? await outsideView(org) : null)) : null;
	return {
		shell: {
			/** Who the pages act as: the signed-in user; null for a signed-out visitor. */
			me: me && {
				id: me.id,
				name: me.name,
				email: me.email,
				is_superadmin: me.is_superadmin,
				avatar_url: me.avatar_url
			},
			/** The organization in view: its ID (in URLs), and what people see it called. */
			org,
			orgName: current?.display_name ?? outside?.display_name ?? org,
			/** Its picture, for the sidebar of a viewer outside it (the switcher has members'). */
			orgAvatar: current?.avatar_url ?? outside?.avatar_url ?? null,
			/**
			 * What the caller may do in it: their role (super admins are admins everywhere), or
			 * `public` from outside it, signed in or not, where only public things can be read.
			 */
			access,
			/** Whether the server has S3 storage, for the sidebar's Storage item. */
			s3: !!config.s3,
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
