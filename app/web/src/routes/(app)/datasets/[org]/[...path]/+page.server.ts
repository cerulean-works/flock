import { error, isHttpError } from '@sveltejs/kit';
import { authConfig } from '#lib/server/auth.ts';
import { datasetsPath, withDetails } from '#lib/server/datasets.ts';
import { change, flock, refused } from '#lib/server/flock.ts';
import type { DatasetAt, DatasetFolder, DatasetInfo } from '#lib/types.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * A folder of the org's flock-datasets (D87), its first page, the rest loaded from the browser;
 * or a dataset (D88) at `?version=` (a number or a tag), else the latest, resolved here once.
 * The tabs read the rest from the browser at the version resolved, and keep their place in the
 * URL (`tab`, `offset`) without running this again.
 *
 * Outside the org there are no folders to browse (D114): the path is a public dataset's, or
 * a 404 (D115).
 */
export const load: PageServerLoad = async ({ params, url, parent }) => {
	const [{ s3 }, { shell }] = await Promise.all([authConfig(), parent()]);
	if (!s3) error(404, 'This Flock server has no S3 storage');
	const path = params.path;
	const base = datasetsPath(params.org);
	if (shell.access === 'public' && !path) error(404, 'Not found');
	const at: DatasetFolder | DatasetAt =
		shell.access === 'public'
			? { kind: 'dataset', path, version: null, updated_at: null }
			: await flock(`${base}/at?${new URLSearchParams({ path })}`).then((r) => r.json());
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

export const actions: Actions = {
	/** Make the dataset public, or private again (D115): org admins only. */
	visibility: async ({ params, request }) => {
		const form = await request.formData();
		const pub = form.get('public') === 'true';
		const res = await change(
			pub ? 'PUT' : 'DELETE',
			`${datasetsPath(params.org)}/public?${new URLSearchParams({ path: params.path })}`
		);
		if (refused(res)) return res;
		return { public: pub };
	}
};
