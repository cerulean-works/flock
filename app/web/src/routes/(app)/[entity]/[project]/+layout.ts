import { projectUrl } from '#lib/data/cache.ts';
import type { ProjectInfo } from '#lib/types.ts';
import type { LayoutLoad } from './$types';

// The project's own record (its description). A layout load, so it runs alongside the page's
// snapshot rather than after it, and only again when the project changes: paging and sorting
// re-run the page load alone.
//
// The workspace works without it, so a failure here isn't an error page: a missing project, or
// an unreachable flock, is reported by the snapshot. (Flocks older than projects/CRUD don't
// serve this endpoint at all.)
export const load: LayoutLoad = async ({ fetch, params }) => {
	const res = await fetch(projectUrl(params.entity, params.project));
	return { project: res.ok ? ((await res.json()) as ProjectInfo) : null };
};
