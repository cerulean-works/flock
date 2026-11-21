import { isHttpError } from '@sveltejs/kit';
import { flock, projectPath } from '#lib/server/flock.ts';
import type { ItemDiff, RunDetail } from '#lib/types.ts';
import type { PageServerLoad } from './$types';

/**
 * Two eval runs compared doc by doc (023 D174): this run as A, and `?with=entity/project/id` as
 * B. Flock runs eval's own diff on the two runs' files and checks the viewer may read both; a
 * pair the diff refuses (another instrument, files that don't match their results) shows why.
 */
export const load: PageServerLoad = async ({ params, url }) => {
	const other = url.searchParams.get('with') ?? '';
	const path = `${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}`;
	let diff: ItemDiff | null = null;
	let refusal: string | null = null;
	try {
		diff = (await (
			await flock(`${path}/diff?with=${encodeURIComponent(other)}`)
		).json()) as ItemDiff;
	} catch (err) {
		if (!isHttpError(err) || err.status !== 409) throw err;
		refusal = err.body.message;
	}
	// Both are readable: Flock answered for the pair.
	const [entity, project, run] = other.split('/');
	const [a, b] = await Promise.all([
		flock(path).then((res) => res.json() as Promise<RunDetail>),
		flock(`${projectPath(entity, project)}/runs/${encodeURIComponent(run)}`).then(
			(res) => res.json() as Promise<RunDetail>
		)
	]);
	return { a, b, diff, refusal };
};
