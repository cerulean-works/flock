import { loadModels } from '#lib/server/models.ts';
import type { PageServerLoad } from './$types';

/** A folder or a model of a project's models (D169; see `loadModels`): members only (D165). */
export const load: PageServerLoad = ({ params, url, parent }) =>
	loadModels(
		{ org: params.entity, project: params.project },
		params.path,
		url,
		parent().then(({ shell }) => shell.access)
	);
