import { relayAnswer } from '#lib/server/datasets.ts';
import { projectPath } from '#lib/server/flock.ts';
import type { RequestHandler } from './$types';

/**
 * The project's runs query (`POST .../runs/query`) as JSON, for the group pages: a draft
 * group's runs and the runs its filters match (D223). A read that uses POST for its body.
 */
export const POST: RequestHandler = async ({ params, request }) =>
	relayAnswer(`${projectPath(params.entity, params.project)}/runs/query`, request);
