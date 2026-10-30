import { error } from '@sveltejs/kit';
import { authConfig } from '#lib/server/auth.ts';
import { flock } from '#lib/server/flock.ts';
import { modelsPath } from '#lib/server/models.ts';
import type { ModelInfo, ModelVersionDetail } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

/**
 * A model (D108) at `?version=` (a number or a tag), else its primary version, resolved here
 * once. The tabs read the rest from the browser at the number resolved, and keep their place in
 * the URL (`tab`) without running this again. A model whose first upload hasn't finished has no
 * version yet.
 */
export const load: PageServerLoad = async ({ params, url }) => {
	const { s3 } = await authConfig();
	if (!s3) error(404, 'This Flock server has no S3 storage');
	const base = `${modelsPath(params.org)}/${encodeURIComponent(params.name)}`;
	const requested = url.searchParams.get('version');
	const info = (await flock(base).then((r) => r.json())) as ModelInfo;
	let version: ModelVersionDetail | null = null;
	if (requested || info.primary !== null) {
		const query = new URLSearchParams(requested ? { ref: requested } : {});
		version = (await flock(`${base}/resolve?${query}`).then((r) => r.json())) as ModelVersionDetail;
	}
	return { info, version, requested, now: Date.now() / 1000 };
};
