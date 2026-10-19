import { fail, isHttpError, redirect } from '@sveltejs/kit';
import { refreshUser } from '#lib/server/auth.ts';
import { removeAvatar, setAvatar } from '#lib/server/avatar.ts';
import { change, refused, flock } from '#lib/server/flock.ts';
import type { Billing, Invite, Member, OrgImport, OrgInfo, Role, UserInfo } from '#lib/types.ts';
import { formText, orgNameError } from '#lib/utils.ts';
import { IMPORT, wandbImport } from '#lib/wandb.ts';
import { orgHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

const orgPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}`;

export const load: PageServerLoad = async ({ depends, params }) => {
	depends(IMPORT);
	const path = orgPath(params.org);
	const [org, members, me, billing, invites, wandb] = await Promise.all([
		flock(path).then((r) => r.json() as Promise<OrgInfo>),
		flock(`${path}/members`).then((r) => r.json() as Promise<{ members: Member[] }>),
		flock('/api/v1/me').then((r) => r.json() as Promise<UserInfo>),
		flock(`${path}/billing`).then((r) => r.json() as Promise<Billing>),
		// Only admins see invitations; members are refused, and the page doesn't list them.
		flock(`${path}/invites`)
			.then((r) => r.json() as Promise<{ invites: Invite[] }>)
			.catch((e) => {
				if (isHttpError(e) && e.status === 403) return null;
				throw e;
			}),
		// Its W&B import: none ever (404), or not for members to see (403).
		flock(`${path}/import`)
			.then((r) => r.json() as Promise<OrgImport>)
			.catch((e) => {
				if (isHttpError(e) && (e.status === 403 || e.status === 404)) return null;
				throw e;
			})
	]);
	return {
		org,
		members: members.members,
		invites: invites?.invites ?? null,
		import: wandb,
		billing,
		me: { id: me.id, is_superadmin: me.is_superadmin },
		now: Date.now() / 1000
	};
};

/** A user id from the form. */
function userId(form: FormData) {
	const id = Number(form.get('user'));
	return Number.isInteger(id) ? id : null;
}

const isRole = (role: unknown): role is Role => role === 'admin' || role === 'member';

/** An amount from the form: blank is none, anything else a number from 0 up. */
function amount(raw: string): number | null | undefined {
	if (!raw) return null;
	const v = Number(raw);
	return Number.isFinite(v) && v >= 0 ? v : undefined;
}

export const actions: Actions = {
	avatar: ({ params, request }) => setAvatar(request, orgPath(params.org)),
	removeAvatar: ({ params }) => removeAvatar(orgPath(params.org)),

	/**
	 * The monthly budget, currency and GPU prices. Prices come as `rate:{type}` fields; a blank
	 * one removes that price. `new_type` and `new_rate` add a type no run has used yet.
	 */
	billing: async ({ params, request }) => {
		const form = await request.formData();
		const budget = amount(formText(form, 'monthly_budget'));
		if (budget === undefined) return fail(400, { error: 'Enter the budget as a number' });
		const currency = formText(form, 'currency').toUpperCase();
		if (!/^[A-Z]{3}$/.test(currency))
			return fail(400, { error: 'Use a three-letter currency code, like USD' });

		const rates: Record<string, number | null> = {};
		for (const [key, value] of form) {
			if (!key.startsWith('rate:')) continue;
			const price = amount(String(value).trim());
			if (price === undefined)
				return fail(400, { error: `Enter the ${key.slice(5)} price as a number` });
			rates[key.slice(5)] = price;
		}
		const type = formText(form, 'new_type');
		if (type) {
			const price = amount(formText(form, 'new_rate'));
			if (price == null) return fail(400, { error: `Enter a price for ${type}` });
			rates[type] = price;
		}
		const res = await change<Billing>('PATCH', `${orgPath(params.org)}/billing`, {
			monthly_budget: budget,
			currency,
			rates
		});
		if (refused(res)) return res;
		return { billing: res.data };
	},

	/** An invitation link, shown this once: Flock keeps only a hash of it. */
	invite: async ({ params, request }) => {
		const form = await request.formData();
		const email = formText(form, 'email');
		const role = form.get('role');
		if (!email) return fail(400, { error: 'Enter their email' });
		if (!isRole(role)) return fail(400, { error: 'Choose a role' });
		const res = await change<Invite & { url: string }>('POST', `${orgPath(params.org)}/invites`, {
			email,
			role
		});
		if (refused(res)) return res;
		const { url, expires_at } = res.data;
		return { invite: { url, email: res.data.email, role, expires_at } };
	},

	revoke: async ({ params, request }) => {
		const id = Number((await request.formData()).get('invite'));
		if (!Number.isInteger(id)) return fail(400, { error: 'No such invitation' });
		const res = await change('DELETE', `${orgPath(params.org)}/invites/${id}`);
		if (refused(res)) return res;
		return { revoked: id };
	},

	role: async ({ params, request }) => {
		const form = await request.formData();
		const user = userId(form);
		const role = form.get('role');
		if (user === null || !isRole(role)) return fail(400, { error: 'No such member' });
		const res = await change('PATCH', `${orgPath(params.org)}/members/${user}`, { role });
		if (refused(res)) return res;
		return { role };
	},

	reset: async ({ request }) => {
		const user = userId(await request.formData());
		if (user === null) return fail(400, { error: 'No such member' });
		const res = await change<{ url: string; expires_at: number }>(
			'POST',
			`/api/v1/users/${user}/password-reset`
		);
		if (refused(res)) return res;
		return { reset: res.data };
	},

	/** Remove a member, or leave (the user's own id). */
	remove: async ({ locals, params, request }) => {
		const user = userId(await request.formData());
		if (user === null) return fail(400, { error: 'No such member' });
		const res = await change('DELETE', `${orgPath(params.org)}/members/${user}`);
		if (refused(res)) return res;
		if (user === locals.user?.id) {
			await refreshUser();
			// A super admin still administers it; anyone else no longer sees it.
			redirect(303, locals.user.is_superadmin ? orgHref(params.org) : '/');
		}
		return { removed: user };
	},

	/** Start a W&B import, or resume one: runs the org already has are skipped. */
	startImport: async ({ params, request }) => {
		const wandb = wandbImport(await request.formData(), params.org);
		if (!wandb) return fail(400, { error: 'Nothing to import' });
		if ('error' in wandb) return fail(400, { error: wandb.error });
		const res = await change<OrgImport>('POST', `${orgPath(params.org)}/import`, wandb.value);
		if (refused(res)) return res;
		return { import: res.data };
	},

	cancelImport: async ({ params }) => {
		const res = await change<OrgImport>('DELETE', `${orgPath(params.org)}/import`);
		if (refused(res)) return res;
		return { import: res.data };
	},

	/** Rename the org: the entity of its projects and runs, so its URLs change too. */
	rename: async ({ params, request }) => {
		const name = formText(await request.formData(), 'name');
		const invalid = orgNameError(name);
		if (invalid) return fail(400, { error: invalid });
		if (name === params.org) return { renamed: name };
		const res = await change<OrgInfo>('PATCH', orgPath(params.org), { name });
		if (refused(res)) return res;
		await refreshUser();
		redirect(303, orgHref(name));
	},

	delete: async ({ locals, params, request }) => {
		const force = (await request.formData()).get('force') === 'on' ? '?force=true' : '';
		const res = await change('DELETE', `${orgPath(params.org)}${force}`);
		if (refused(res)) return res;
		await refreshUser();
		redirect(303, locals.user?.is_superadmin ? '/admin' : '/');
	}
};
