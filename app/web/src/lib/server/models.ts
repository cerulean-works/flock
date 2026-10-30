import type { ModelEvalRun, ModelVersionDetail, RunDetail } from '#lib/types.ts';
import { relayAnswer } from './datasets.ts';
import { flockAnswer } from './flock.ts';

/** Flock's routes for an org's models (014). */
export const modelsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/models`;

/** Pass a request from the browser on to one of the org's model routes, as the signed-in user. */
export const relayModels = (org: string, op: string, request: Request) =>
	relayAnswer(`${modelsPath(org)}/${op}`, request);

// Runs read at once when filling in a version's evals.
const RUNS_AT_ONCE = 8;

/**
 * The eval runs of version `number` of a model, each with its numeric summary, for the Evals
 * tab. A run that can't be read (deleted since) is left out.
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
			const got = await flockAnswer(
				`/api/v1/projects/${encodeURIComponent(entity)}/${encodeURIComponent(project)}/runs/${encodeURIComponent(id)}`
			);
			if (!got.ok) {
				await got.body?.cancel();
				continue;
			}
			const run = (await got.json()) as RunDetail;
			const metrics: Record<string, number> = {};
			for (const [key, value] of Object.entries(run.summary ?? {}))
				if (typeof value === 'number' && Number.isFinite(value)) metrics[key] = value;
			runs[i] = {
				path: paths[i],
				entity,
				project,
				id,
				display_name: run.display_name,
				state: run.state,
				created_at: run.created_at,
				metrics
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
