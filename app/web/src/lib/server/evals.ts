import { isEvalResults } from '#lib/evals.ts';
import type { EvalResults } from '#lib/types.ts';
import { flockAnswer } from './flock.ts';

/**
 * An eval run's `results.json` (023 D173), read through the run's file route as the signed-in
 * user, so the project's access check applies. Null when the run has none, or one the pages
 * can't show (not JSON, or the wrong shape), or Flock fails to answer: a page then goes without
 * it rather than failing whole.
 */
export async function readEvalResults(run: string): Promise<EvalResults | null> {
	// `run` is the run's path in Flock's API, `/api/v1/projects/{entity}/{project}/runs/{id}`.
	const res = await flockAnswer(`${run}/files/results.json`);
	if (!res.ok) {
		await res.body?.cancel();
		return null;
	}
	const found: unknown = await res.json().catch(() => null);
	return isEvalResults(found) ? found : null;
}
