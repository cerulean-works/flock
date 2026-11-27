import { error, isHttpError } from '@sveltejs/kit';
import { type Scope, scopeQuery } from '../scope.ts';
import type {
	Access,
	DatasetAt,
	DatasetFolder,
	DatasetInfo,
	DatasetLocation,
	DatasetsPath
} from '../types.ts';
import { authConfig } from './auth.ts';
import { flock, flockAnswer } from './flock.ts';

/** Flock's routes for an org's datasets (D94). */
export const datasetsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/datasets`;

/**
 * A path of a scope's datasets (D169): a folder (D87), its first page with each dataset's size
 * (Flock's `details`), the rest loaded from the browser; or a dataset (D88) at `?version=` (a
 * number or a tag), else the latest, resolved here once. The tabs read the rest from the browser
 * at the version resolved, and keep their place in the URL (`tab`, `offset`) without running
 * this again.
 *
 * Outside the org there are no folders to browse (D114): the path is a public dataset's, or a
 * 404 (D115). A project's datasets are never public (D165).
 */
export async function loadDatasets(
	scope: Scope,
	path: string,
	url: URL,
	viewer: Promise<Access>
): Promise<DatasetsPath> {
	const [{ s3 }, access] = await Promise.all([authConfig(), viewer]);
	if (!s3) error(404, { message: 'This Flock server has no S3 storage', noS3: true });
	const outside = access === 'public';
	if (outside && (!path || scope.project !== null)) error(404, 'Not found');
	const base = datasetsPath(scope.org);
	const query = scopeQuery(scope);
	const at: DatasetFolder | DatasetAt = outside
		? { kind: 'dataset', path, version: null, updated_at: null }
		: await flock(`${base}/at?${new URLSearchParams({ path, details: 'true', ...query })}`).then(
				(r) => r.json()
			);
	const now = Date.now() / 1000;
	if (at.kind === 'folder') return { kind: 'folder', path, folder: at, now };

	const requested = url.searchParams.get('version');
	const search = new URLSearchParams({
		path,
		...(requested ? { version: requested } : {}),
		...query
	});
	// A project's dataset is under its project's folder (D160), which only the API knows: asked
	// alongside the dataset, for Browse files and Access in S3.
	const [read, key] = await Promise.all([
		flock(`${base}/info?${search}`)
			.then((r) => r.json() as Promise<DatasetInfo>)
			.then(
				(info) => ({ info, failed: null }),
				(e: unknown) => {
					// Lance couldn't read it (a corrupt manifest, a missing file): the page says so
					// in place of the tabs, and keeps its actions.
					if (isHttpError(e) && e.status === 502) return { info: null, failed: e.body.message };
					throw e;
				}
			),
		scope.project === null || outside
			? path
			: flock(`${base}/location?${new URLSearchParams({ path, ...query })}`)
					.then((r) => r.json() as Promise<DatasetLocation>)
					.then((where) => where.key)
	]);
	return { kind: 'dataset', path, ...read, requested, s3, key, now };
}

/**
 * Pass a request from the browser on to one of the org's dataset routes (`op`, with the
 * request's query), as the signed-in user.
 */
export const relayDatasets = (org: string, op: string, request: Request) =>
	relayAnswer(`${datasetsPath(org)}/${op}`, request);

/**
 * Pass a request from the browser on to Flock at `path` (with the request's query, and its
 * `forward` headers), as the signed-in user. Flock's answer comes back with its status, and a
 * refusal as `{message, …}` with whatever else Flock said (`taken`, `version`, `command`),
 * which the dialogs need.
 */
export async function relayAnswer(
	path: string,
	request: Request,
	forward: readonly string[] = []
): Promise<Response> {
	const url = new URL(request.url);
	const method = request.method;
	const body =
		method === 'POST' || method === 'PUT' || method === 'PATCH' ? await request.text() : undefined;
	const sent: Record<string, string> =
		body === undefined ? {} : { 'content-type': 'application/json' };
	for (const name of forward) {
		const value = request.headers.get(name);
		if (value) sent[name] = value;
	}
	const res = await flockAnswer(`${path}${url.search}`, { method, headers: sent, body });
	const headers = { 'content-type': 'application/json', 'cache-control': 'no-store' };
	if (res.status === 204) return new Response(null, { status: 204, headers });
	const json = (await res.json().catch(() => null)) as Record<string, unknown> | null;
	if (res.ok) return Response.json(json, { status: res.status, headers });
	const { error, errors, detail, ...rest } = json ?? {};
	void errors;
	const message =
		typeof error === 'string'
			? error
			: typeof detail === 'string'
				? detail
				: Array.isArray(detail)
					? String((detail[0] as { msg?: string })?.msg ?? '').replace(/^Value error, /, '')
					: `Flock answered ${res.status}`;
	return Response.json({ message, ...rest }, { status: res.status, headers });
}
