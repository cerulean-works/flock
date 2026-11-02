import { isHttpError } from '@sveltejs/kit';
import { projectPath, flock } from '#lib/server/flock.ts';
import { runActions } from '#lib/server/runs.ts';
import type { Billing, RunDetail, RunHistory, RunModels } from '#lib/types.ts';
import type { Actions, PageServerLoad } from './$types';

/** Points per curve: plenty for one run's panels, and a few MB at most. */
const MAX_POINTS = 2000;

/** Something the page can go without: missing (or not allowed) becomes null. */
const optional = <T>(promise: Promise<T>) =>
	promise.catch((err: unknown) => {
		if (isHttpError(err) && err.status < 500) return null;
		throw err;
	});

/**
 * A run with its curves, whoever logged it and the org's currency. The curves are read before
 * the run itself: live updates resume from the run's line counts, so points logged in between
 * are streamed again rather than missed twice over.
 *
 * Outside the org there's no price (the run's cost is null, D113) and no checkpoints to list:
 * neither is asked for.
 */
export const load: PageServerLoad = async ({ params, parent }) => {
	const path = `${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}`;
	const curves = (stream: 'history' | 'system') =>
		flock(`${path}/history?stream=${stream}&max_points=${MAX_POINTS}`).then(
			(res) => res.json() as Promise<RunHistory>
		);
	const member = parent().then(({ shell }) => shell.access !== 'public');
	const [history, system, billing, models] = await Promise.all([
		curves('history'),
		curves('system'),
		member.then((yes) =>
			yes
				? optional(
						flock(`/api/v1/orgs/${encodeURIComponent(params.entity)}/billing`).then(
							(res) => res.json() as Promise<Billing>
						)
					)
				: null
		),
		// The model versions it made (D108), for the Checkpoints section.
		member.then((yes) =>
			yes ? optional(flock(`${path}/models`).then((res) => res.json() as Promise<RunModels>)) : null
		)
	]);
	const run = (await (await flock(path)).json()) as RunDetail;
	const owner = run.owner_user ?? null;
	return {
		run,
		history,
		system,
		owner: owner && { id: owner.id, name: owner.name, avatar_url: owner.avatar_url },
		currency: billing?.currency ?? null,
		models,
		/** Relative times are from the server's clock, so SSR and hydration agree. */
		now: Date.now() / 1000
	};
};

/** Editing, moving and deleting the run, as from the project's runs table. */
export const actions: Actions = runActions;
