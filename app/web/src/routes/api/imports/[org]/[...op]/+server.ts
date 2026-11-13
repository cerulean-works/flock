import { error } from '@sveltejs/kit';
import { relayImports } from '#lib/server/imports.ts';
import type { RequestHandler } from './$types';

/** The import routes the browser may reach through here (D124). */
const ROUTES = /^hf(\/(preview|\d+))?$/;

/**
 * The org's Hugging Face imports, for the import dialogs and the rows that follow them: the
 * browser never reaches Flock itself, so its calls come through here.
 */
const relay: RequestHandler = async ({ params, request }) => {
	if (!ROUTES.test(params.op)) error(404, 'Not found');
	return relayImports(params.org, params.op.slice('hf/'.length), request);
};

export const GET = relay;
export const POST = relay;
export const DELETE = relay;
