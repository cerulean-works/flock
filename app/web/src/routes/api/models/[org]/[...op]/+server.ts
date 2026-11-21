import { error } from '@sveltejs/kit';
import { relayModels } from '#lib/server/models.ts';
import type { RequestHandler } from './$types';

/** A model path (D158): segments of D98's names, joined by `/`. */
const PATH = '[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*';

/**
 * The model routes the browser may reach through here (014, D163): a folder's listing (no
 * `op`), empty folders, the trash, and a model's routes, `{path}/-` and `{path}/-/{op}`.
 */
const ROUTES = new RegExp(
	`^(|-/folders|-/trash|-/trash/(models|versions)/\\d+(/restore)?|${PATH}/-(/(versions|versions/\\d+|resolve|primary|tags/[^/]+))?)$`
);

/**
 * The org's model routes, for the Models tab's listings, the model page's tabs and dialogs and
 * the org page's trash: the browser never reaches Flock itself, so its calls come through here.
 * The scope's `project` comes along in the query.
 */
const relay: RequestHandler = async ({ params, request }) => {
	if (!ROUTES.test(params.op)) error(404, 'Not found');
	const op = params.op.split('/').map(encodeURIComponent).join('/');
	return relayModels(params.org, op, request);
};

export const GET = relay;
export const POST = relay;
export const PUT = relay;
export const PATCH = relay;
export const DELETE = relay;
