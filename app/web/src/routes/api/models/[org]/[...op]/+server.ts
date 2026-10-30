import { error } from '@sveltejs/kit';
import { evalRuns, relayModels } from '#lib/server/models.ts';
import type { RequestHandler } from './$types';

/** The model routes the browser may reach through here (014). */
const ROUTES =
	/^(-\/trash|-\/trash\/(models|versions)\/\d+(\/restore)?|[a-z0-9][a-z0-9._-]*(\/(versions|versions\/\d+|resolve|primary|tags\/[^/]+))?)$/;
/** The dashboard's own route: a version's eval runs with their summaries. */
const EVALS = /^([a-z0-9][a-z0-9._-]*)\/versions\/(\d+)\/evals$/;

/**
 * The org's model routes, for the model page's tabs and dialogs and the org page's trash: the
 * browser never reaches Flock itself, so its calls come through here.
 */
const relay: RequestHandler = async ({ params, request }) => {
	const evals = EVALS.exec(params.op);
	if (evals && request.method === 'GET') return evalRuns(params.org, evals[1], Number(evals[2]));
	if (!ROUTES.test(params.op)) error(404, 'Not found');
	const op = params.op.split('/').map(encodeURIComponent).join('/');
	return relayModels(params.org, op, request);
};

export const GET = relay;
export const POST = relay;
export const PUT = relay;
export const DELETE = relay;
