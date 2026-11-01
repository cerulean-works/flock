import { membersUrl, projectUrl } from '#lib/data/cache.ts';
import type { Member, ProjectInfo } from '#lib/types.ts';
import type { LayoutLoad } from './$types';

// The project's own record (its description), and the org's members (who logged its runs). A
// layout load, so it runs alongside the page's snapshot rather than after it, and only again
// when the project changes: paging and sorting re-run the page load alone.
//
// The workspace works without them, so a failure here isn't an error page: a missing project,
// or an unreachable flock, is reported by the snapshot. (Flocks older than projects/CRUD don't
// serve the project endpoint at all.)
export const load: LayoutLoad = async ({ fetch, params }) => {
	const [project, members] = await Promise.all([
		fetch(projectUrl(params.entity, params.project)),
		fetch(membersUrl(params.entity))
	]);
	return {
		project: project.ok ? ((await project.json()) as ProjectInfo) : null,
		members: members.ok ? ((await members.json()) as { members: Member[] }).members : []
	};
};
