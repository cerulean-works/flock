import { isEvalResults } from '#lib/evals.ts';
import type { EvalResults } from '#lib/types.ts';
import { flockAnswer } from './flock.ts';

/**
 * An eval run's `results.json` (025 D184), read through the run's file route as the signed-in
 * user, so the project's access check applies. Null when the run has none, or one the pages
 * can't show (over 16 MiB, not JSON, or the wrong shape), or Flock fails to answer: a page then
 * goes without it rather than failing whole.
 */
export async function readEvalResults(run: string): Promise<EvalResults | null> {
	// `run` is the run's path in Flock's API, `/api/v1/projects/{entity}/{project}/runs/{id}`.
	const res = await flockAnswer(`${run}/files/results.json`);
	// A Base Easy run's file is about 100 KiB; a far bigger one isn't parsed at all, since every
	// run page asks for it. Flock's file route always sends the length.
	const size = Number(res.headers.get('content-length'));
	if (!res.ok || !(size <= 16 << 20)) {
		await res.body?.cancel();
		return null;
	}
	// An own `__proto__` key would pass the check but fail the page's serialization, so none is
	// kept.
	const found: unknown = await res
		.text()
		.then((text) => JSON.parse(text, (key, value) => (key === '__proto__' ? undefined : value)))
		.catch(() => null);
	return isEvalResults(found) ? found : null;
}
