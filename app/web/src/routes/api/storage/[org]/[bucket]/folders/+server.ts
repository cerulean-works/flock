import { bucketPath, relay } from '#lib/server/storage.ts';
import type { RequestHandler } from './$types';

/** A new, empty folder: `{prefix}` ending in `/`. */
export const POST: RequestHandler = ({ params, request }) =>
	relay(`${bucketPath(params.org, params.bucket)}/folders`, request);
