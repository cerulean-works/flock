import { isHttpError } from '@sveltejs/kit';
import { projectPath, flock } from '#lib/server/flock.ts';
import { runActions } from '#lib/server/runs.ts';
import type { Billing, Member, RunDetail, RunHistory } from '#lib/types.ts';
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
 * A run with its curves, the name of whoever logged it and the org's currency. The curves are
 * read before the run itself: live updates resume from the run's line counts, so points logged
 * in between are streamed again rather than missed twice over.
 */
export const load: PageServerLoad = async ({ params }) => {
	const path = `${projectPath(params.entity, params.project)}/runs/${encodeURIComponent(params.run)}`;
	const curves = (stream: 'history' | 'system') =>
		flock(`${path}/history?stream=${stream}&max_points=${MAX_POINTS}`).then(
			(res) => res.json() as Promise<RunHistory>
		);
	const [history, system, members, billing] = await Promise.all([
		curves('history'),
		curves('system'),
		optional(
			flock(`/api/v1/orgs/${encodeURIComponent(params.entity)}/members`).then(
				(res) => res.json() as Promise<{ members: Member[] }>
			)
		),
		optional(
			flock(`/api/v1/orgs/${encodeURIComponent(params.entity)}/billing`).then(
				(res) => res.json() as Promise<Billing>
			)
		)
	]);
	const run = (await (await flock(path)).json()) as RunDetail;
	const owner = members?.members.find((m) => m.id === run.owner) ?? null;
	return {
		run,
		history,
		system,
		owner: owner && { id: owner.id, name: owner.name, avatar_url: owner.avatar_url },
		currency: billing?.currency ?? null,
		/** Relative times are from the server's clock, so SSR and hydration agree. */
		now: Date.now() / 1000
	};
};

/** Editing, moving and deleting the run, as from the project's runs table. */
export const actions: Actions = runActions;
