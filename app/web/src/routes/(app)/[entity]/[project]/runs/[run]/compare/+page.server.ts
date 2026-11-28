import { error } from '@sveltejs/kit';
import { flock, flockAnswer, projectPath } from '#lib/server/flock.ts';
import type { ItemDiff, RunDetail, RunHistory } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

/** Points per curve: two runs' panels, a few MB at most. */
const MAX_POINTS = 1500;

const runPath = (entity: string, project: string, run: string) =>
	`${projectPath(entity, project)}/runs/${encodeURIComponent(run)}`;

/** A run's curves, both streams; a run whose curves can't be read charts nothing. */
async function curves(path: string) {
	const read = (stream: 'history' | 'system') =>
		flock(`${path}/history?stream=${stream}&max_points=${MAX_POINTS}`)
			.then((res) => res.json() as Promise<RunHistory>)
			.catch((): RunHistory => ({ x: '_step', series: {} }));
	const [history, system] = await Promise.all([read('history'), read('system')]);
	return { history, system };
}

/**
 * Two runs compared, from any projects: this run as A, and `?with=entity/project/id` as B. Every
 * pair gets its charts, summary, config and system side by side; two eval runs also get eval's
 * doc-by-doc diff (025 D185), which Flock runs on the two runs' files. A pair it can't diff (a
 * run with no results file, another instrument) shows why. The diff and the curves stream in
 * after the page (031 D209: the diff takes 5 to 6 s on two whole-bank runs). Without `?with=`
 * there's only A, and the page asks for B (the run page's **Compare with…**).
 */
export const load: PageServerLoad = async ({ params, url }) => {
	const other = url.searchParams.get('with') ?? '';
	const path = runPath(params.entity, params.project, params.run);
	if (!other) {
		const a = (await (await flock(path)).json()) as RunDetail;
		return { a, b: null, compared: null, curves: null };
	}
	// Both runs first, so one the viewer can't read is the usual 404 (or sign-in), as on its page.
	const parts = other.split('/');
	const [entity, project, run] = parts;
	if (parts.length !== 3 || !entity || !project || !run) error(404, `run ${other} not found`);
	const otherPath = runPath(entity, project, run);
	const [a, b] = await Promise.all([
		flock(path).then((res) => res.json() as Promise<RunDetail>),
		flock(otherPath).then((res) => res.json() as Promise<RunDetail>)
	]);
	const read = Promise.all([curves(path), curves(otherPath)]).then(([ca, cb]) => ({
		a: ca,
		b: cb
	}));
	read.catch(() => {});
	if (a.job_type !== 'eval' || b.job_type !== 'eval') return { a, b, compared: null, curves: read };
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
	return { a, b, compared, curves: read };
};
