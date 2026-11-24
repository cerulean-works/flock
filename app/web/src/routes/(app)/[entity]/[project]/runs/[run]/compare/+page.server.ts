import { error } from '@sveltejs/kit';
import { flock, flockAnswer, projectPath } from '#lib/server/flock.ts';
import type { ItemDiff, RunDetail } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

/**
 * Two eval runs compared doc by doc (025 D185): this run as A, and `?with=entity/project/id` as
 * B. Flock runs eval's own diff on the two runs' files. A pair it can't compare (a run with no
 * results file, another instrument, files that don't match their results) shows why. The diff
 * streams in after the page (031 D209: 5 to 6 s on two whole-bank runs). Without
 * `?with=` there's only A, and the page asks for B (the run page's **Compare with…**).
 */
export const load: PageServerLoad = async ({ params, url }) => {
	const other = url.searchParams.get('with') ?? '';
	const path = `${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}`;
	if (!other) {
		const a = (await (await flock(path)).json()) as RunDetail;
		return { a, b: null, compared: null };
	}
	// Both runs first, so one the viewer can't read is the usual 404 (or sign-in), as on its page.
	const parts = other.split('/');
	const [entity, project, run] = parts;
	if (parts.length !== 3 || !entity || !project || !run) error(404, `run ${other} not found`);
	const [a, b] = await Promise.all([
		flock(path).then((res) => res.json() as Promise<RunDetail>),
		flock(`${projectPath(entity, project)}/runs/${encodeURIComponent(run)}`).then(
			(res) => res.json() as Promise<RunDetail>
		)
	]);
	// The diff comes after the page, streamed: on two whole-bank runs it reads hundreds of MB of
	// predictions for seconds, and a linked task's items need only the task's name to start.
	const compared = flockAnswer(`${path}/diff?with=${encodeURIComponent(other)}`).then(
		async (res) => {
			if (res.ok) return { diff: (await res.json()) as ItemDiff, refusal: null };
			const body = (await res.json().catch(() => null)) as { error?: string } | null;
			// With both runs readable, a 404 means a run has no results file.
			if (res.status !== 404 && res.status !== 409)
				error(res.status as 500, body?.error ?? `Flock responded ${res.status}`);
			return { diff: null, refusal: body?.error ?? `Flock responded ${res.status}` };
		}
	);
	// SvelteKit catches a streamed promise only once it renders. A diff failing before then would
	// be an unhandled rejection, which ends the server; the page still reads the failure.
	compared.catch(() => {});
	return { a, b, compared };
};
