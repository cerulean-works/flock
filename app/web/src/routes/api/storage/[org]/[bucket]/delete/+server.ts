import { bucketPath, relay } from '#lib/server/storage.ts';
import type { RequestHandler } from './$types';

/** Delete objects (`keys`) and folders (`prefixes`), up to Flock's cap of 10,000 objects. */
export const POST: RequestHandler = ({ params, request }) =>
	relay(`${bucketPath(params.org, params.bucket)}/objects/delete`, request);
