import { relayAnswer } from '#lib/server/datasets.ts';
import { projectPath } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** Each value of some run columns with how many runs have it: what their headers filter to. */
export const GET: RequestHandler = ({ params, request }) =>
	relayAnswer(`${projectPath(params.entity, params.project)}/runs/values`, request);
