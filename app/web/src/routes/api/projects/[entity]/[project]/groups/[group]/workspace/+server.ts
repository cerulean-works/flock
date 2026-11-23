import { error } from '@sveltejs/kit';
import { projectPath } from '#lib/server/flock.ts';
import { relaySnapshot } from '#lib/server/snapshot.ts';
import type { RequestHandler } from './$types';

/**
 * One page of a group's runs as Flock's binary workspace snapshot (025): the Runs page's view,
 * pointed at the group's members (see `relaySnapshot`).
 */
export const GET: RequestHandler = ({ params, url }) => {
	if (!/^\d+$/.test(params.group)) error(404, 'Not found');
	const path = `${projectPath(params.entity, params.project)}/groups/${params.group}/workspace`;
	return relaySnapshot(path, url);
};
