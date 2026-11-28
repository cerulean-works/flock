import { json } from '@sveltejs/kit';
import { flock, projectPath } from '#lib/server/flock.ts';
import { flockFilter } from '#lib/workspace/filters.ts';
import { CHOICES, rankChoices, reachesOut } from '#lib/run-choices.ts';
import type { ProjectInfo, RunChoice } from '#lib/types.ts';
import type { RequestHandler } from './$types';

/** How many of each other project's matches are read: the list shows the best few overall. */
const PER_PROJECT = 8;
/** The other projects searched: the most recently active, so a large org stays quick. */
const PROJECTS = 40;

/**
 * Runs to compare with, any job type: the project's newest, searched by name and id as the runs
 * table does (`?q=`). From three letters on, the org's other projects are searched too, and all
 * of them ranked in one list (`rankChoices`).
 */
export const GET: RequestHandler = async ({ params, url }) => {
	const q = url.searchParams.get('q') ?? '';
	const { entity, project } = params;
	const search = flockFilter(q, []);

	const runsOf = async (e: string, p: string, limit: number): Promise<RunChoice[]> => {
		const res = await flock(`${projectPath(e, p)}/runs/query`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({
				...(search ? { filter: search } : {}),
				sort: ['-created_at'],
				limit,
				columns: ['display_name', 'created_at', 'state', 'job_type', 'config.model']
			})
		});
		if (!res.ok) return [];
		const page = (await res.json()) as { ids: string[]; data: Record<string, unknown[]> };
		return page.ids.map((id, i) => ({
			entity: e,
			project: p,
			id,
			display_name: (page.data.display_name?.[i] as string | null) ?? null,
			created_at: page.data.created_at?.[i] as number,
			state: page.data.state?.[i] as string,
			job_type: (page.data.job_type?.[i] as string | null) ?? null,
			model: (page.data['config.model']?.[i] as string | null) ?? null
		}));
	};

	const others = async (): Promise<RunChoice[]> => {
		if (!reachesOut(q)) return [];
		const res = await flock(`/api/v1/projects?${new URLSearchParams({ entity })}`);
		if (!res.ok) return [];
		const { projects } = (await res.json()) as { projects: ProjectInfo[] };
		const searched = projects
			.filter((p) => p.name !== project && p.run_count > 0)
			.sort((a, b) => b.updated_at - a.updated_at)
			.slice(0, PROJECTS);
		const found = await Promise.all(searched.map((p) => runsOf(p.entity, p.name, PER_PROJECT)));
		return found.flat();
	};

	const [own, rest] = await Promise.all([runsOf(entity, project, CHOICES), others()]);
	const runs = rankChoices(q, { entity, project }, [...own, ...rest]);
	return json({ runs }, { headers: { 'cache-control': 'no-store' } });
};
