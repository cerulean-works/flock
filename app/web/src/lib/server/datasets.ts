import { flockAnswer } from './flock.ts';

/** Flock's routes for an org's datasets (D94). */
export const datasetsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/datasets`;

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
