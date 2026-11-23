import { projectPath } from '#lib/server/flock.ts';
import { relayAnswer } from '#lib/server/datasets.ts';
import type { RequestHandler } from './$types';

/**
 * Each run's per-token values over one doc's choices, for an opened item's heat (029), relayed
 * with its query (`doc`, `task`, `keep`, `with`), which Flock checks.
 */
export const GET: RequestHandler = ({ params, request }) =>
	relayAnswer(
		`${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}/item/tokens`,
		request
	);
