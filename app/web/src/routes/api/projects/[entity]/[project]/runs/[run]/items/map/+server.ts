import { projectPath } from '#lib/server/flock.ts';
import { relayAnswer } from '#lib/server/datasets.ts';
import type { RequestHandler } from './$types';

/**
 * Every doc of one task for two runs, by cell, for the compare page's map (029), relayed with its
 * query (`task`, `with`), which Flock checks. A refusal comes back as `{message}`.
 */
export const GET: RequestHandler = ({ params, request }) =>
	relayAnswer(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/items/map`,
		request
	);
