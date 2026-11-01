import { bucketPath, relay } from '#lib/server/storage.ts';
import type { RequestHandler } from './$types';

/** The bucket with its history, for the overview's history sheet. */
export const GET: RequestHandler = ({ params }) => relay(bucketPath(params.org, params.bucket));
