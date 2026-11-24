import { datasetsPath, loadDatasets } from '#lib/server/datasets.ts';
import { change, refused } from '#lib/server/flock.ts';
import type { Actions, PageServerLoad } from './$types';

/** A folder or a dataset of the org's global datasets (see `loadDatasets`). */
export const load: PageServerLoad = ({ params, url, parent }) => {
	const access = parent().then(({ shell }) => shell.access);
	return loadDatasets({ org: params.org, project: null }, params.path, url, access);
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
