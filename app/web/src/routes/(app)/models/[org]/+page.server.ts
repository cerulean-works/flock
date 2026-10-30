import { error } from '@sveltejs/kit';
import { authConfig } from '#lib/server/auth.ts';
import { flock } from '#lib/server/flock.ts';
import { modelsPath } from '#lib/server/models.ts';
import type { ModelEntry } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

/** The org's models (D108), by name. Flock creates its flock-models here if it's missing. */
export const load: PageServerLoad = async ({ params }) => {
	const { s3 } = await authConfig();
	if (!s3) error(404, 'This Flock server has no S3 storage');
	const { models } = (await flock(modelsPath(params.org)).then((r) => r.json())) as {
		models: ModelEntry[];
	};
	return { models, now: Date.now() / 1000 };
};
