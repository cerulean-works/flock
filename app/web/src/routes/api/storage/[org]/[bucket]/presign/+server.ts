import { bucketPath, relay } from '#lib/server/storage.ts';
import type { RequestHandler } from './$types';

/** Presigned URLs to the S3 proxy, for downloads, previews and uploads (D69). */
export const POST: RequestHandler = ({ params, request }) =>
	relay(`${bucketPath(params.org, params.bucket)}/presign`, request);
