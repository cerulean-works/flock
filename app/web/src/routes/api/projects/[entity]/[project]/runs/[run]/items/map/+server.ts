import { projectPath } from '#lib/server/flock.ts';
import { relayAnswer } from '#lib/server/datasets.ts';
import type { RequestHandler } from './$types';

/**
 * Every doc of a suite's tasks for two runs, by cell, for one square of the compare page (029),
 * relayed with its query (`suite`, `with`), which Flock checks. A refusal comes back as `{message}`.
 */
export const GET: RequestHandler = ({ params, request }) =>
	relayAnswer(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/items/map`,
		request
	);
