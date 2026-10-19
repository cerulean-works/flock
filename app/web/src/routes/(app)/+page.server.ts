import { fail, redirect } from '@sveltejs/kit';
import { refreshUser } from '#lib/server/auth.ts';
import { change, refused, flock } from '#lib/server/flock.ts';
import { FEED } from '#lib/data/feed.svelte.ts';
import type { Budget, FeedPage } from '#lib/types.ts';
import { formText, monthOf } from '#lib/utils.ts';
import { entityHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * Home: the activity of the organization in the sidebar (the user's default), and its budget.
 * `?feed=mine` keeps the feed to the user's own runs; `?month=YYYY-MM` shows an earlier month.
 */
export const load: PageServerLoad = async ({ parent, url, depends }) => {
	depends(FEED);
	const { shell } = await parent();
	const now = Date.now() / 1000;
	const thisMonth = monthOf(now);
	if (!shell.org) return { org: null, now, thisMonth, feed: null, budget: null, query: {} };

	const mine = url.searchParams.get('feed') === 'mine';
	const month = url.searchParams.get('month');
	const query: Record<string, string> = { org: shell.org, ...(mine && { owner: 'me' }) };
	const budgetSearch = month && /^\d{4}-\d{2}$/.test(month) ? `?month=${month}` : '';
	const [feed, budget] = await Promise.all([
		flock(`/api/v1/feed?${new URLSearchParams(query)}`).then(
			(r) => r.json() as Promise<FeedPage>
		),
		flock(`/api/v1/orgs/${encodeURIComponent(shell.org)}/budget${budgetSearch}`).then(
			(r) => r.json() as Promise<Budget>
		)
	]);
	return { org: shell.org, now, thisMonth, feed, budget, query, mine };
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
