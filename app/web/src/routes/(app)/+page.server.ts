import { fail, redirect } from '@sveltejs/kit';
import { refreshUser } from '#lib/server/auth.ts';
import { change, flock, refused } from '#lib/server/flock.ts';
import { runsOf } from '#lib/home/runs.ts';
import { clusterGlance, slurmConfigured } from '#lib/server/slurm.ts';
import { LIVE_STATES, type Budget, type FeedPage } from '#lib/types.ts';
import { formText, monthOf } from '#lib/utils.ts';
import { entityHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/** How far back the team's tile looks for runs that ended. */
const TEAM_DAYS = 3;

const RUN_KINDS = 'run_started,run_finished,run_failed,run_crashed';

/**
 * Home (H1): the way in. The viewer's own runs first (live, gone wrong, latest), the org's
 * month beside them, and the command field over all of it. Old links that filtered Home's
 * feed (`?feed=mine`, `?project=`, `?month=`) go on to the org's Runs, which took them over.
 * Without an org there is nothing to show but how to get one.
 */
export const load: PageServerLoad = async ({ parent, url }) => {
	const { shell } = await parent();
	if (!shell.org) return { org: null };
	if (['feed', 'project', 'month'].some((k) => url.searchParams.has(k))) {
		const to = new URLSearchParams();
		if (url.searchParams.get('feed') === 'mine') to.set('owner', 'me');
		for (const key of ['project', 'month']) {
			const value = url.searchParams.get(key);
			if (value) to.set(key, value);
		}
		redirect(307, `${entityHref(shell.org)}${to.size ? `?${to}` : ''}`);
	}
	const org = shell.org;
	const read = <T>(path: string) =>
		flock(path)
			.then((r) => (r.ok ? (r.json() as Promise<T>) : null))
			.catch(() => null);
	const feed = (q: Record<string, string>) =>
		read<FeedPage>(`/api/v1/feed?${new URLSearchParams({ org, ...q })}`);
	const now = Date.now() / 1000;
	const [mine, everyone, budget, cluster] = await Promise.all([
		feed({ owner: 'me', kinds: RUN_KINDS, limit: '100' }),
		// Everyone's, for the team's tile: what teammates have going and just finished.
		feed({ kinds: RUN_KINDS, limit: '100' }),
		read<Budget>(`/api/v1/orgs/${encodeURIComponent(org)}/budget`),
		// The Slurm dashboard, when Flock is told where it is.
		clusterGlance()
	]);
	return {
		org,
		mine: runsOf(mine),
		team: runsOf(everyone).filter(
			(r) =>
				r.owner?.id !== shell.me?.id &&
				(LIVE_STATES.has(r.state) || r.at > now - TEAM_DAYS * 86_400)
		),
		budget,
		cluster,
		/** Configured but not answering: the ops line says so. */
		clusterDown: !cluster && slurmConfigured(),
		thisMonth: monthOf(now),
		/** Relative times are from the server's clock, so SSR and hydration agree. */
		now
	};
};

export const actions: Actions = {
	/** The sidebar's switcher: make an org the default, then go back or to its runs. */
	workspace: async ({ request }) => {
		const form = await request.formData();
		const org = formText(form, 'org');
		if (!org) return fail(400, { error: 'Choose an organization' });
		const res = await change('PATCH', '/api/v1/me', { default_org: org });
		if (refused(res)) return res;
		await refreshUser();
		const next = formText(form, 'next');
		redirect(303, next.startsWith('/') && !next.startsWith('//') ? next : entityHref(org));
	}
};
