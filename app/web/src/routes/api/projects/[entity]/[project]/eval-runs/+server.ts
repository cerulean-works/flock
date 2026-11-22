import { json } from '@sveltejs/kit';
import { flock, projectPath } from '#lib/server/flock.ts';
import { flockFilter } from '#lib/workspace/filters.ts';
import type { EvalRunChoice } from '#lib/types.ts';
import type { RequestHandler } from './$types';

/** How many a picker lists: the newest, narrowed by typing. */
const LIMIT = 30;

/**
 * A project's eval runs, newest first, for the compare page's run picker (025 D185). `?q=`
 * searches names and ids as the runs table does.
 */
export const GET: RequestHandler = async ({ params, url }) => {
	const search = flockFilter(url.searchParams.get('q') ?? '', []);
	const evals = { job_type: 'eval' };
	const res = await flock(`${projectPath(params.entity, params.project)}/runs/query`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({
			filter: search ? { $and: [evals, search] } : evals,
			sort: ['-created_at'],
			limit: LIMIT,
			columns: ['display_name', 'created_at', 'state', 'config.model']
		})
	});
	const page = (await res.json()) as {
		ids: string[];
		data: Record<string, unknown[]>;
	};
	const runs: EvalRunChoice[] = page.ids.map((id, i) => ({
		id,
		display_name: (page.data.display_name?.[i] as string | null) ?? null,
		created_at: page.data.created_at?.[i] as number,
		state: page.data.state?.[i] as string,
		model: (page.data['config.model']?.[i] as string | null) ?? null
	}));
	return json({ runs }, { headers: { 'cache-control': 'no-store' } });
};
