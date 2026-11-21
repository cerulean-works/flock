import { loadDatasets } from '#lib/server/datasets.ts';
import type { PageServerLoad } from './$types';

/** A folder or a dataset of a project's datasets (D169; see `loadDatasets`): members only (D165). */
export const load: PageServerLoad = ({ params, url, parent }) =>
	loadDatasets(
		{ org: params.entity, project: params.project },
		params.path,
		url,
		parent().then(({ shell }) => shell.access)
	);
