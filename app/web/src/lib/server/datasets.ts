import type { DatasetFolder, DatasetInfo } from '#lib/types.ts';
import { flockAnswer } from './flock.ts';

/** Flock's routes for an org's datasets (D94). */
export const datasetsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/datasets`;

// Datasets whose info is read at once when filling in a folder's listing.
const DETAILS_AT_ONCE = 8;

/**
 * A page of a folder with each dataset's size, and its version when the listing has none, read
 * from the dataset's info. A dataset whose info can't be read keeps a null size.
 */
export async function withDetails(org: string, folder: DatasetFolder): Promise<DatasetFolder> {
	const datasets = [...folder.datasets];
	let next = 0;
	async function work() {
		while (next < datasets.length) {
			const i = next++;
			const d = datasets[i];
			const query = new URLSearchParams({ path: d.path });
			const res = await flockAnswer(`${datasetsPath(org)}/info?${query}`);
			const info = res.ok ? ((await res.json()) as DatasetInfo) : null;
			if (!res.ok) await res.body?.cancel();
			datasets[i] = {
				...d,
				version: d.version ?? info?.version ?? null,
				size: info?.data_size ?? null
			};
		}
	}
	await Promise.all(Array.from({ length: Math.min(DETAILS_AT_ONCE, datasets.length) }, work));
	return { ...folder, datasets };
}

/**
 * Pass a request from the browser on to one of the org's dataset routes (`op`, with the
 * request's query), as the signed-in user.
 */
export const relayDatasets = (org: string, op: string, request: Request) =>
	relayAnswer(`${datasetsPath(org)}/${op}`, request);

/**
 * Pass a request from the browser on to Flock at `path` (with the request's query), as the
 * signed-in user. Flock's answer comes back with its status, and a refusal as `{message, …}`
 * with whatever else Flock said (`taken`, `version`, `command`), which the dialogs need.
 */
export async function relayAnswer(path: string, request: Request): Promise<Response> {
	const url = new URL(request.url);
	const method = request.method;
	const body = method === 'POST' || method === 'PUT' ? await request.text() : undefined;
	const res = await flockAnswer(`${path}${url.search}`, {
		method,
		headers: body === undefined ? {} : { 'content-type': 'application/json' },
		body
	});
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
