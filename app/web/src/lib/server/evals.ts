import { error } from '@sveltejs/kit';
import type { EvalResults } from '#lib/types.ts';
import { flockAnswer } from './flock.ts';

/**
 * An eval run's `results.json` (023 D173), read through the run's file route as the signed-in
 * user, so the project's access check applies. Null when the run has none, or it isn't JSON.
 */
export async function readEvalResults(run: string): Promise<EvalResults | null> {
	// `run` is the run's path in Flock's API, `/api/v1/projects/{entity}/{project}/runs/{id}`.
	const res = await flockAnswer(`${run}/files/results.json`);
	if (res.status === 404) {
		await res.body?.cancel();
		return null;
	}
	if (!res.ok) error(res.status as 500, `Flock responded ${res.status} for results.json`);
	return (await res.json().catch(() => null)) as EvalResults | null;
}
