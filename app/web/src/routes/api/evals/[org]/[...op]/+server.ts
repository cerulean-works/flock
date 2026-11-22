import { error } from '@sveltejs/kit';
import { relayEvals } from '#lib/server/launches.ts';
import type { RequestHandler } from './$types';

/** The eval routes the browser may reach through here, by method (026). */
const ROUTES: Record<string, RegExp> = {
	GET: /^launches(\/(catalogue|\d+))?$/,
	POST: /^launches$/,
	DELETE: /^launches\/\d+$/
};

/**
 * The org's eval launches, for the launch dialog and the rows that follow them: the browser
 * never reaches Flock itself, so its calls come through here.
 */
const relay: RequestHandler = async ({ params, request }) => {
	if (!ROUTES[request.method]?.test(params.op)) error(404, 'Not found');
	return relayEvals(params.org, params.op, request);
};

export const GET = relay;
export const POST = relay;
export const DELETE = relay;
