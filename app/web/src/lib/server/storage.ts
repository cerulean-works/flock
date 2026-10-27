import { flock } from './flock.ts';

/** Flock's routes for an org's bucket (D69). */
export const bucketPath = (org: string, bucket: string) =>
	`/api/v1/orgs/${encodeURIComponent(org)}/buckets/${encodeURIComponent(bucket)}`;

/**
 * Pass a request from the browser on to one of a bucket's routes, as the signed-in user, and
 * Flock's JSON answer back. A refusal answers `{message}` with Flock's status, as `error` does.
 */
export async function relay(path: string, request?: Request): Promise<Response> {
	const init: RequestInit = request
		? {
				method: request.method,
				headers: { 'content-type': 'application/json' },
				body: await request.text()
			}
		: {};
	const res = await flock(path, init);
	return new Response(res.body, {
		status: res.status,
		headers: { 'content-type': 'application/json', 'cache-control': 'no-store' }
	});
}
