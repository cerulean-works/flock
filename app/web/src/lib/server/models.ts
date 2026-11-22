import { error } from '@sveltejs/kit';
import { RESULT_SCHEMAS, evalScores } from '#lib/evals.ts';
import { type Scope, scopeQuery } from '../scope.ts';
import type {
	Access,
	ModelAt,
	ModelEvalRun,
	ModelFolder,
	ModelInfo,
	ModelsPath,
	ModelVersionDetail,
	RunDetail
} from '../types.ts';
import { authConfig } from './auth.ts';
import { relayAnswer } from './datasets.ts';
import { readEvalResults } from './evals.ts';
import { flock, flockAnswer, projectPath } from './flock.ts';

/** Flock's routes for an org's models (014). */
export const modelsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/models`;

/** Pass a request from the browser on to one of the org's model routes, as the signed-in user. */
export const relayModels = (org: string, op: string, request: Request) =>
	relayAnswer(`${modelsPath(org)}${op ? `/${op}` : ''}`, request);

/**
 * A path of a scope's models (D169), for the Models tabs: the folder's first page (D158), or a
 * model at `?version=` (a number or a tag), else its primary version, resolved here once. The
 * API decides which it is: listing a model's path answers `kind: "model"`. The model page's tabs
 * read the rest from the browser at the number resolved, and keep their place in the URL
 * (`tab`) without running this again. A model whose first upload hasn't finished has no version
 * yet. A project's models are never shown outside the org (D165).
 */
export async function loadModels(
	scope: Scope,
	path: string,
	url: URL,
	viewer: Promise<Access>
): Promise<ModelsPath> {
	const [{ s3 }, access] = await Promise.all([authConfig(), viewer]);
	if (!s3) error(404, 'This Flock server has no S3 storage');
	if (access === 'public' && scope.project !== null) error(404, 'Not found');
	const query = scopeQuery(scope);
	const at = (await flock(
		`${modelsPath(scope.org)}?${new URLSearchParams({ folder: path, ...query })}`
	).then((r) => r.json())) as ModelFolder | ModelAt;
	const now = Date.now() / 1000;
	if (at.kind !== 'model') return { kind: 'folder', path, folder: at, now };

	const base = `${modelsPath(scope.org)}/${path.split('/').map(encodeURIComponent).join('/')}/-`;
	const requested = url.searchParams.get('version');
	const info = (await flock(`${base}?${new URLSearchParams(query)}`).then((r) =>
		r.json()
	)) as ModelInfo;
	let version: ModelVersionDetail | null = null;
	if (requested || info.primary !== null) {
		const search = new URLSearchParams({ ...(requested ? { ref: requested } : {}), ...query });
		version = (await flock(`${base}/resolve?${search}`).then((r) =>
			r.json()
		)) as ModelVersionDetail;
	}
	return { kind: 'model', path, info, version, requested, now };
}

// Runs read at once when filling in a version's evals.
const RUNS_AT_ONCE = 8;

/**
 * The eval runs of version `number` of the model `name` in `scope`, each with the scores its
 * results file gives (025 D183), for the Evals tab. A run that can't be read (deleted since) is
 * left out; one with no results file, or one that names another version, has no scores.
 */
export async function evalRuns(scope: Scope, name: string, number: number): Promise<Response> {
	const model = name.split('/').map(encodeURIComponent).join('/');
	const query = new URLSearchParams(scopeQuery(scope)).toString();
	const res = await flockAnswer(
		`${modelsPath(scope.org)}/${model}/-/versions/${number}${query && `?${query}`}`
	);
	if (!res.ok) return relayRefusal(res);
	const version = (await res.json()) as ModelVersionDetail;
	const paths = version.evals ?? []; // none from an API before 025
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
				// A run linked to two versions (its config changed) has one results file: its
				// scores show only on the version that file names.
				scores:
					results &&
					RESULT_SCHEMAS.includes(results.schema_version) &&
					results.provenance.model === `flock:${version.model}@${version.number}` &&
					results.provenance.model_digest === version.digest &&
					// A project's model is written without its org (D162); schema 10 names it.
					(results.provenance.model_org ?? scope.org) === scope.org
						? evalScores(results)
						: null
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
