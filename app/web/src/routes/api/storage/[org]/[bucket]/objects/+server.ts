import { bucketPath, relay } from '#lib/server/storage.ts';
import type { RequestHandler } from './$types';

/** One page of a folder: `?prefix=` (the name filter appended), and `&cursor=` for the next. */
export const GET: RequestHandler = ({ params, url }) => {
	const search = new URLSearchParams();
	for (const name of ['prefix', 'cursor', 'limit']) {
		const value = url.searchParams.get(name);
		if (value) search.set(name, value);
	}
	return relay(`${bucketPath(params.org, params.bucket)}/objects?${search}`);
};
