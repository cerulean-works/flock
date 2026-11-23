import { membersUrl, ownersUrl, projectUrl } from '#lib/data/cache.ts';
import { GROUPS, groupsApi, type GroupList, type Suggestion } from '#lib/groups.ts';
import type { Owner, ProjectInfo } from '#lib/types.ts';
import type { LayoutLoad } from './$types';

// The project's own record (its description, its visibility and the viewer's access), and who
// logged its runs: the org's members, or for a viewer outside the org (who can't list them), the
// project's owners without their emails (D113). A layout load, so it runs alongside the page's
// snapshot rather than after it, and only again when the project changes: paging and sorting
// re-run the page load alone.
//
// The workspace works without them, so a failure here isn't an error page: a missing project,
// or an unreachable flock, is reported by the snapshot. (Flocks older than projects/CRUD don't
// serve the project endpoint at all.)
//
// The project's groups and suggested groups are read here too, for the sidebar's Groups (D182)
// and the group pages: a change to a group invalidates `GROUPS`, which runs this again. Members
// only are offered suggestions, which they'd save as groups. Flocks without groups serve
// neither, and the sidebar shows none.
export const load: LayoutLoad = async ({ fetch, params, parent, depends }) => {
	depends(GROUPS);
	const read = async <T>(url: string) => {
		const res = await fetch(url);
		return res.ok ? ((await res.json()) as T) : null;
	};
	// The shell already knows whether the viewer is outside the org: ask for the right list
	// alongside the project, not after it.
	// Suggestions are streamed, not awaited: on a cold cache they take seconds to cluster (D186),
	// and the Runs page shouldn't wait on them. The sidebar shows them when they arrive.
	const suggestions = parent()
		.then(async ({ shell }) =>
			shell.access === 'public'
				? []
				: ((
						await read<{ suggestions: Suggestion[] }>(
							groupsApi(params.entity, params.project, 'suggestions')
						)
					)?.suggestions ?? [])
		)
		.catch((): Suggestion[] => []);
	const [project, people, groups] = await Promise.all([
		read<ProjectInfo>(projectUrl(params.entity, params.project)),
		parent().then(async ({ shell }) =>
			shell.access === 'public'
				? (await read<{ owners: Owner[] }>(ownersUrl(params.entity, params.project)))?.owners
				: (await read<{ members: Owner[] }>(membersUrl(params.entity)))?.members
		),
		read<GroupList>(groupsApi(params.entity, params.project))
	]);
	return { project, people: people ?? [], groups, suggestions };
};
