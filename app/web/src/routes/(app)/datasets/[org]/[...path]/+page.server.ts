import { error, isHttpError } from '@sveltejs/kit';
import { authConfig } from '#lib/server/auth.ts';
import { datasetsPath, withDetails } from '#lib/server/datasets.ts';
import { flock } from '#lib/server/flock.ts';
import type { DatasetAt, DatasetFolder, DatasetInfo } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

/**
 * A folder of the org's flock-datasets (D87), its first page, the rest loaded from the browser;
 * or a dataset (D88) at `?version=` (a number or a tag), else the latest, resolved here once.
 * The tabs read the rest from the browser at the version resolved, and keep their place in the
 * URL (`tab`, `offset`) without running this again.
 */
export const load: PageServerLoad = async ({ params, url }) => {
	const { s3 } = await authConfig();
	if (!s3) error(404, 'This Flock server has no S3 storage');
	const path = params.path;
	const base = datasetsPath(params.org);
	const at = (await flock(`${base}/at?${new URLSearchParams({ path })}`).then((r) => r.json())) as
		DatasetFolder | DatasetAt;
	const now = Date.now() / 1000;
	if (at.kind === 'folder') {
		const folder = await withDetails(params.org, at);
		return { kind: 'folder' as const, path, folder, now };
	}

	const requested = url.searchParams.get('version');
	const query = new URLSearchParams({ path, ...(requested ? { version: requested } : {}) });
	try {
		const info = (await flock(`${base}/info?${query}`).then((r) => r.json())) as DatasetInfo;
		return { kind: 'dataset' as const, path, info, failed: null, requested, s3, now };
	} catch (e) {
		// Lance couldn't read it (a corrupt manifest, a missing file): the page says so in
		// place of the tabs, and keeps its actions.
		if (isHttpError(e) && e.status === 502) {
			return {
				kind: 'dataset' as const,
				path,
				info: null,
				failed: e.body.message,
				requested,
				s3,
				now
			};
		}
		throw e;
	}
};
