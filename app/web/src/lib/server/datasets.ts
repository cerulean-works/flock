import { flockAnswer } from './flock.ts';

/** Flock's routes for an org's datasets (D94). */
export const datasetsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/datasets`;

/**
 * Pass a request from the browser on to one of the org's dataset routes (`op`, with the
 * request's query), as the signed-in user. Flock's answer comes back with its status, and a
 * refusal as `{message, …}` with whatever else Flock said (`taken`, `version`, `command`), which
 * the dialogs need.
 */
export async function relayDatasets(org: string, op: string, request: Request): Promise<Response> {
	const url = new URL(request.url);
	const method = request.method;
	const body = method === 'POST' || method === 'PUT' ? await request.text() : undefined;
	const res = await flockAnswer(`${datasetsPath(org)}/${op}${url.search}`, {
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
