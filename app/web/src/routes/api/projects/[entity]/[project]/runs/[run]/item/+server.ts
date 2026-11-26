import { projectPath } from '#lib/server/flock.ts';
import { relayAnswer } from '#lib/server/datasets.ts';
import type { RequestHandler } from './$types';

/**
 * One item's whole text and answers, which an items row opens to (028), relayed with its query
 * (`doc`, `task`, `keep`, `with`). A refusal comes back as `{message}`.
 */
export const GET: RequestHandler = ({ params, request }) =>
	relayAnswer(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/item`,
		request
	);
