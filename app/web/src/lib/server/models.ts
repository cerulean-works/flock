import { error } from '@sveltejs/kit';
import { type Scope, scopeQuery } from '../scope.ts';
import type {
	Access,
	ModelAt,
	ModelFolder,
	ModelInfo,
	ModelsPath,
	ModelVersionDetail
} from '../types.ts';
import { authConfig } from './auth.ts';
import { relayAnswer } from './datasets.ts';
import { flock } from './flock.ts';

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
