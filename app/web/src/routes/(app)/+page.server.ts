import { fail, redirect } from '@sveltejs/kit';
import { refreshUser } from '#lib/server/auth.ts';
import { change, refused, flock } from '#lib/server/flock.ts';
import { FEED, feedKinds } from '#lib/data/feed.svelte.ts';
import type { Budget, FeedPage } from '#lib/types.ts';
import { formText, monthOf } from '#lib/utils.ts';
import { entityHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * Home: the activity of the organization in the sidebar (the user's default), and its budget.
 * `?feed=mine` keeps the feed to the user's own runs, `?kinds=` to some kinds of activity and
 * `?project=` to one project; `?month=YYYY-MM` shows an earlier month.
 */
export const load: PageServerLoad = async ({ parent, url, depends }) => {
	depends(FEED);
	const { shell } = await parent();
	const now = Date.now() / 1000;
	const thisMonth = monthOf(now);
	if (!shell.org)
		return { org: null, now, thisMonth, feed: null, budget: null, query: {}, kinds: null };

	const mine = url.searchParams.get('feed') === 'mine';
	const kinds = feedKinds(url.searchParams.get('kinds'));
	const project = url.searchParams.get('project') || null;
	const month = url.searchParams.get('month');
	const query: Record<string, string> = {
		org: shell.org,
		...(mine && { owner: 'me' }),
		...(kinds && { kinds }),
		...(project && { project })
	};
	const budgetSearch = month && /^\d{4}-\d{2}$/.test(month) ? `?month=${month}` : '';
	const [feed, budget] = await Promise.all([
		flock(`/api/v1/feed?${new URLSearchParams(query)}`).then((r) => r.json() as Promise<FeedPage>),
		flock(`/api/v1/orgs/${encodeURIComponent(shell.org)}/budget${budgetSearch}`).then(
			(r) => r.json() as Promise<Budget>
		)
	]);
	return { org: shell.org, now, thisMonth, feed, budget, query, mine, kinds, project };
};

/** The API path of a budget warning's dismissal, from a form naming the org and the warning. */
function dismissalPath(form: FormData): string | null {
	const org = formText(form, 'org');
	const warning = formText(form, 'warning');
	if (!org || !warning) return null;
	return `/api/v1/orgs/${encodeURIComponent(org)}/budget/warnings/${encodeURIComponent(warning)}/dismissal`;
}

export const actions: Actions = {
	/** Dismiss a budget warning for everyone in the org (admins), as it was shown. */
	dismissWarning: async ({ request }) => {
		const form = await request.formData();
		const path = dismissalPath(form);
		if (!path) return fail(400, { error: 'No such warning' });
		const res = await change('PUT', path, { fingerprint: formText(form, 'fingerprint') });
		if (refused(res)) return res;
		return { dismissed: true };
	},

	/** Show a dismissed budget warning again (admins). */
	restoreWarning: async ({ request }) => {
		const path = dismissalPath(await request.formData());
		if (!path) return fail(400, { error: 'No such warning' });
		const res = await change('DELETE', path);
		if (refused(res)) return res;
		return { restored: true };
	},

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
