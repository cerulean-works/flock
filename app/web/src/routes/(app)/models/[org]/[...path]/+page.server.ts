import { loadModels } from '#lib/server/models.ts';
import type { PageServerLoad } from './$types';

/**
 * A folder or a model of the org's global models (see `loadModels`). Every `/models/{org}/{name}`
 * from before folders is a one-segment path, and still opens the model.
 */
export const load: PageServerLoad = ({ params, url, parent }) =>
	loadModels(
		{ org: params.org, project: null },
		params.path,
		url,
		parent().then(({ shell }) => shell.access)
	);
