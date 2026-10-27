import { authConfig } from '#lib/server/auth.ts';
import { flock } from '#lib/server/flock.ts';
import { bucketPath } from '#lib/server/storage.ts';
import { PAGE, keyOfPath } from '#lib/storage.ts';
import type { Listing, ObjectDetails } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

// A folder's URL ends in `/` (D63): keep it, rather than SvelteKit's default of dropping it.
export const trailingSlash = 'ignore';

/**
 * A bucket's root or one of its folders (D65): the first page of its listing, the rest loaded
 * from the browser; or an object (D67): its details, previews loaded from the browser.
 */
export const load: PageServerLoad = async ({ params, url }) => {
	const key = keyOfPath(params.path, url.pathname);
	const path = bucketPath(params.org, params.bucket);
	const { s3 } = await authConfig();
	if (!key || key.endsWith('/')) {
		const search = new URLSearchParams({ prefix: key, limit: String(PAGE) });
		const listing = (await flock(`${path}/objects?${search}`).then((r) => r.json())) as Listing;
		return { kind: 'folder' as const, key, listing, s3, object: null, now: Date.now() / 1000 };
	}
	const object = (await flock(`${path}/object?${new URLSearchParams({ key })}`).then((r) =>
		r.json()
	)) as ObjectDetails;
	return { kind: 'object' as const, key, listing: null, s3, object, now: Date.now() / 1000 };
};
