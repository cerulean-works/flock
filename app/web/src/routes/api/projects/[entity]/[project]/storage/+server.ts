import { relayAnswer } from '#lib/server/datasets.ts';
import { projectPath } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/** What deleting a project deletes with it (D167), for its delete dialog: members only. */
export const GET: RequestHandler = ({ params, request }) =>
	relayAnswer(`${projectPath(params.entity, params.project)}/storage`, request);
