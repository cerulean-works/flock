import type { ModelEvalRun, ModelVersionDetail, RunDetail } from '#lib/types.ts';
import { RESULT_SCHEMAS, evalScores } from '#lib/evals.ts';
import { relayAnswer } from './datasets.ts';
import { readEvalResults } from './evals.ts';
import { flockAnswer, projectPath } from './flock.ts';

/** Flock's routes for an org's models (014). */
export const modelsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/models`;

/** Pass a request from the browser on to one of the org's model routes, as the signed-in user. */
export const relayModels = (org: string, op: string, request: Request) =>
	relayAnswer(`${modelsPath(org)}/${op}`, request);

// Runs read at once when filling in a version's evals.
const RUNS_AT_ONCE = 8;

/**
 * The eval runs of version `number` of a model, each with the scores its results file gives
 * (023 D172), for the Evals tab. A run that can't be read (deleted since) is left out; one with
 * no results file has no scores.
 */
export async function evalRuns(org: string, name: string, number: number): Promise<Response> {
	const res = await flockAnswer(
		`${modelsPath(org)}/${encodeURIComponent(name)}/versions/${number}`
	);
	if (!res.ok) return relayRefusal(res);
	const version = (await res.json()) as ModelVersionDetail;
	const paths = version.evals;
	const runs: (ModelEvalRun | null)[] = paths.map(() => null);
	let next = 0;
	async function work() {
		while (next < paths.length) {
			const i = next++;
			const [entity, project, id] = paths[i].split('/');
			const path = `${projectPath(entity, project)}/runs/${encodeURIComponent(id)}`;
			const got = await flockAnswer(path);
			if (!got.ok) {
				await got.body?.cancel();
				continue;
			}
			const run = (await got.json()) as RunDetail;
			const results = await readEvalResults(path);
			runs[i] = {
				path: paths[i],
				entity,
				project,
				id,
				display_name: run.display_name,
				state: run.state,
				created_at: run.created_at,
				scores:
					results && RESULT_SCHEMAS.includes(results.schema_version) ? evalScores(results) : null
			};
		}
	}
	await Promise.all(Array.from({ length: Math.min(RUNS_AT_ONCE, paths.length) }, work));
	return Response.json(
		{ runs: runs.filter((r) => r !== null) },
		{ headers: { 'cache-control': 'no-store' } }
	);
}

/** A refusal from Flock as the dialogs read one: `{message}` with its status. */
async function relayRefusal(res: Response): Promise<Response> {
	const json = (await res.json().catch(() => null)) as { error?: string } | null;
	return Response.json(
		{ message: json?.error ?? `Flock answered ${res.status}` },
		{ status: res.status, headers: { 'cache-control': 'no-store' } }
	);
}
