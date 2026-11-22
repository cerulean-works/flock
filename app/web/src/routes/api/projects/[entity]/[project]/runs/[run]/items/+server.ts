import { projectPath } from '#lib/server/flock.ts';
import { relayAnswer } from '#lib/server/datasets.ts';
import type { RequestHandler } from './$types';

/**
 * A page of one task's items for the eval pages (027), relayed with its query (`task`, `keep`,
 * `with`, `offset`, `limit`), which Flock checks. A refusal comes back as `{message}`, so a page
 * can say why a run has no text.
 */
export const GET: RequestHandler = ({ params, request }) =>
	relayAnswer(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/items`,
		request
	);
