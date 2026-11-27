import { relay } from '#lib/server/storage.ts';
import type { RequestHandler } from './$types';

/** The org's buckets, for the command menu's Buckets. */
export const GET: RequestHandler = ({ params }) =>
	relay(`/api/v1/orgs/${encodeURIComponent(params.org)}/buckets`);
