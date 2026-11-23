import { error } from '@sveltejs/kit';
import { relayAnswer } from '#lib/server/datasets.ts';
import { projectPath } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/**
 * The group routes the browser may reach through here (025, D194): the list (no `op`), the
 * project's settings, Compare, suggestions, the assistant and its proposals, and one group's
 * routes.
 */
const ROUTES =
	/^(|settings|compare|suggestions|suggestions\/dismiss|assist|propose|\d+|\d+\/(runs\/query|columns|new|primary))$/;

/**
 * A project's run groups, for the group pages, the sidebar and their changes: the browser never
 * reaches Flock itself, so its calls come through here. Refusals come back as `{message}` with
 * Flock's status (an assistant that's off answers 404, D191).
 */
const relay: RequestHandler = async ({ params, request }) => {
	if (!ROUTES.test(params.op)) error(404, 'Not found');
	const path = `${projectPath(params.entity, params.project)}/groups`;
	return relayAnswer(params.op ? `${path}/${params.op}` : path, request);
};

export const GET = relay;
export const POST = relay;
export const PUT = relay;
export const PATCH = relay;
export const DELETE = relay;
