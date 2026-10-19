import { fail, redirect } from '@sveltejs/kit';
import { refreshUser } from '#lib/server/auth.ts';
import { change, refused, flock } from '#lib/server/flock.ts';
import type { OrgInfo, UserInfo } from '#lib/types.ts';
import { formText, orgNameError } from '#lib/utils.ts';
import { orgHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

// Flock refuses everyone but super admins (403), which the error page shows.
export const load: PageServerLoad = async () => {
	const [users, orgs] = await Promise.all([
		flock('/api/v1/users').then((r) => r.json() as Promise<{ users: UserInfo[] }>),
		// Every organization, for a super admin.
		flock('/api/v1/orgs').then((r) => r.json() as Promise<{ orgs: OrgInfo[] }>)
	]);
	return { users: users.users, orgs: orgs.orgs, now: Date.now() / 1000 };
};

function userId(form: FormData) {
	const id = Number(form.get('user'));
	return Number.isInteger(id) ? id : null;
}

export const actions: Actions = {
	/** A new organization, with its creator as its first admin: on to inviting people. */
	createOrg: async ({ request }) => {
		const name = formText(await request.formData(), 'name');
		const invalid = orgNameError(name);
		if (invalid) return fail(400, { error: invalid });
		const res = await change<OrgInfo>('POST', '/api/v1/orgs', { name });
		if (refused(res)) return res;
		await refreshUser();
		redirect(303, orgHref(name));
	},

	superadmin: async ({ locals, request }) => {
		const form = await request.formData();
		const user = userId(form);
		if (user === null) return fail(400, { error: 'No such user' });
		const is_superadmin = form.get('value') === 'true';
		const res = await change<UserInfo>('PATCH', `/api/v1/users/${user}`, { is_superadmin });
		if (refused(res)) return res;
		if (user === locals.user?.id) {
			await refreshUser();
			// Giving it up leaves nothing here for you.
			if (!is_superadmin) redirect(303, '/account');
		}
		return { user: res.data };
	},

	reset: async ({ request }) => {
		const user = userId(await request.formData());
		if (user === null) return fail(400, { error: 'No such user' });
		const res = await change<{ url: string; expires_at: number }>(
			'POST',
			`/api/v1/users/${user}/password-reset`
		);
		if (refused(res)) return res;
		return { reset: res.data };
	},

	deleteUser: async ({ request }) => {
		const user = userId(await request.formData());
		if (user === null) return fail(400, { error: 'No such user' });
		const res = await change('DELETE', `/api/v1/users/${user}`);
		if (refused(res)) return res;
		return { deleted: user };
	}
};
