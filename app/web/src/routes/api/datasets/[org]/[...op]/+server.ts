import { error } from '@sveltejs/kit';
import { relayDatasets } from '#lib/server/datasets.ts';
import type { RequestHandler } from './$types';

/** The dataset routes the browser may reach through here (D94). */
const ROUTES =
	/^(at|info|rows|cell|versions|readme|delete|rename|trash|trash\/\d+(\/restore)?|tags\/[^/]+)$/;

/**
 * The org's dataset routes, for the dataset page's tabs and dialogs and the org page's trash:
 * the browser never reaches Flock itself, so its calls come through here.
 */
const relay: RequestHandler = ({ params, request }) => {
	if (!ROUTES.test(params.op)) error(404, 'Not found');
	const op = params.op.split('/').map(encodeURIComponent).join('/');
	return relayDatasets(params.org, op, request);
};

export const GET = relay;
export const POST = relay;
export const PUT = relay;
export const DELETE = relay;
