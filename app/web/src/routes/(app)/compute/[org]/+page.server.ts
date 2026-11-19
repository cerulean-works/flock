import { error } from '@sveltejs/kit';
import { loadCompute } from '#lib/server/compute.ts';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, parent, fetch, depends }) => {
	const { shell } = await parent();
	if (!shell.me || shell.access === 'public') error(403, 'Workspace membership required');
	depends('compute:snapshot');
	return { compute: await loadCompute(params.org, fetch) };
};
