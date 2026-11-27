import { fail, redirect } from '@sveltejs/kit';
import { refreshUser } from '#lib/server/auth.ts';
import { change, refused } from '#lib/server/flock.ts';
import { formText } from '#lib/utils.ts';
import { entityHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * The way in: the runs of the organization in the sidebar (the user's default), which hold its
 * budget too. Home's old links keep their filters: `?feed=mine` becomes `?runs=mine`, and
 * `?project=` and `?month=` carry over. Without an org there is nothing to show but how to
 * get one.
 */
export const load: PageServerLoad = async ({ parent, url }) => {
	const { shell } = await parent();
	if (!shell.org) return { org: null };
	const to = new URLSearchParams();
	if (url.searchParams.get('feed') === 'mine') to.set('runs', 'mine');
	for (const key of ['project', 'month']) {
		const value = url.searchParams.get(key);
		if (value) to.set(key, value);
	}
	const search = to.size ? `?${to}` : '';
	redirect(307, `${entityHref(shell.org)}${search}`);
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
