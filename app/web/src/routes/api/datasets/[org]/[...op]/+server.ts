import { error } from '@sveltejs/kit';
import { relayDatasets, withDetails } from '#lib/server/datasets.ts';
import type { DatasetFolder } from '#lib/types.ts';
import type { RequestHandler } from './$types';

/** The dataset routes the browser may reach through here (D94). */
const ROUTES =
	/^(at|info|rows|cell|versions|readme|delete|rename|trash|trash\/\d+(\/restore)?|tags\/[^/]+)$/;

/**
 * The org's dataset routes, for the dataset page's tabs and dialogs and the org page's trash:
 * the browser never reaches Flock itself, so its calls come through here.
 */
const relay: RequestHandler = async ({ params, request }) => {
	if (!ROUTES.test(params.op)) error(404, 'Not found');
	const op = params.op.split('/').map(encodeURIComponent).join('/');
	const res = await relayDatasets(params.org, op, request);
	// A folder's later pages get the sizes and versions its first page had.
	if (op !== 'at' || request.method !== 'GET' || !res.ok) return res;
	const at = (await res.json()) as DatasetFolder | { kind: 'dataset' };
	if (at.kind !== 'folder') return Response.json(at, { headers: res.headers });
	return Response.json(await withDetails(params.org, at), { headers: res.headers });
};

export const GET = relay;
export const POST = relay;
export const PUT = relay;
export const DELETE = relay;
