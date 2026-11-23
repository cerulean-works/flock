import { projectPath } from '#lib/server/flock.ts';
import { relaySnapshot } from '#lib/server/snapshot.ts';
import type { RequestHandler } from './$types';

/** One page of the project's runs as Flock's binary workspace snapshot (see `relaySnapshot`). */
export const GET: RequestHandler = ({ params, url }) =>
	relaySnapshot(`${projectPath(params.entity, params.project)}/workspace`, url);
