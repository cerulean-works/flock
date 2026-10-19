import { error, fail, isActionFailure, redirect, type ActionFailure } from '@sveltejs/kit';
import { FLOCK_API_URL } from '$app/env/private';
import { getRequestEvent } from '$app/server';
import { endSession, loginHref } from './session.ts';

/**
 * Flock's API. The dashboard talks to it only from the server, as the signed-in
 * user (see hooks.server.ts), so Flock's address and the user's tokens never reach the
 * browser.
 */

function authorized(init: RequestInit): RequestInit {
	const headers = new Headers(init.headers);
	const { token } = getRequestEvent().locals;
	if (token) headers.set('authorization', `Bearer ${token}`);
	return { ...init, headers };
}

/**
 * Flock no longer accepts the session (it ended elsewhere, or the user was removed):
 * forget it and sign in again. Pages redirect; this server's own API answers 401, which the
 * page loads calling it turn into the same redirect.
 */
function signedOut(): never {
	const event = getRequestEvent();
	endSession(event.cookies);
	if (event.url.pathname.startsWith('/api/')) error(401, 'Sign in to continue');
	redirect(303, loginHref(event.url));
}

/**
 * Flock's error body: `{error}` from its own checks; `{detail}` from FastAPI, a list of
 * problems for an invalid request or a plain string (`"Not Found"` for an unknown route).
 */
interface ErrorBody {
	error?: string;
	ids?: string[];
	detail?: string | { msg?: string }[];
}

function messageOf(body: ErrorBody | null, status: number): string {
	const detail = body?.detail;
	const message =
		body?.error ??
		(typeof detail === 'string' ? detail : detail?.[0]?.msg?.replace(/^Value error, /, ''));
	return message || `Flock responded ${status}`;
}

/** A read. Failures become error pages: there is nothing to show without the data. */
export async function flock(path: string, init: RequestInit = {}): Promise<Response> {
	let res: Response;
	try {
		res = await fetch(`${FLOCK_API_URL}${path}`, authorized(init));
	} catch (cause) {
		if (init.signal?.aborted) throw cause;
		console.error(`flock unreachable at ${FLOCK_API_URL}`, cause);
		error(502, `Flock at ${FLOCK_API_URL} is not reachable`);
	}
	if (res.status === 401 && !path.startsWith('/api/v1/auth/')) signedOut();
	if (!res.ok) {
		const body = (await res.json().catch(() => null)) as ErrorBody | null;
		error(res.status as 400, messageOf(body, res.status));
	}
	return res;
}

/** Why Flock refused a change, for the form that asked for it. */
export interface Refusal {
	error: string;
	/** Runs in the way, e.g. active runs a delete would need `force` for. */
	ids?: string[];
}

/**
 * A change, from a form action. Refusals (a conflict, an invalid name, an unreachable flock)
 * come back as action failures for the form to show, rather than as error pages. The body goes
 * as JSON, except a file (an avatar), which goes as its raw bytes.
 */
export async function change<T = null>(
	method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
	path: string,
	body?: unknown
): Promise<{ data: T } | ActionFailure<Refusal>> {
	const raw = body instanceof Blob;
	let res: Response;
	try {
		res = await fetch(
			`${FLOCK_API_URL}${path}`,
			authorized({
				method,
				headers:
					body === undefined
						? {}
						: {
								'content-type': raw ? body.type || 'application/octet-stream' : 'application/json'
							},
				body: body === undefined ? undefined : raw ? body : JSON.stringify(body)
			})
		);
	} catch (cause) {
		console.error(`flock unreachable at ${FLOCK_API_URL}`, cause);
		return fail(502, { error: 'Flock is not reachable' });
	}
	if (res.status === 401 && !path.startsWith('/api/v1/auth/')) signedOut();
	const json = res.status === 204 ? null : await res.json().catch(() => null);
	if (!res.ok) {
		const err = json as ErrorBody | null;
		return fail(res.status, { error: messageOf(err, res.status), ids: err?.ids });
	}
	return { data: json as T };
}

/** Whether `change` was refused. */
export const refused = <T>(
	res: { data: T } | ActionFailure<Refusal>
): res is ActionFailure<Refusal> => isActionFailure(res);

export const projectPath = (entity: string, project: string) =>
	`/api/v1/projects/${encodeURIComponent(entity)}/${encodeURIComponent(project)}`;
