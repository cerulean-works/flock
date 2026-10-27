import { fail, isHttpError, redirect } from '@sveltejs/kit';
import { authConfig, refreshUser } from '#lib/server/auth.ts';
import { removeAvatar, setAvatar } from '#lib/server/avatar.ts';
import { change, refused, flock } from '#lib/server/flock.ts';
import type {
	Billing,
	BudgetWarnings,
	DeletedProject,
	Invite,
	Member,
	OrgImport,
	OrgInfo,
	Role,
	UserInfo
} from '#lib/types.ts';
import { formText, orgDisplayNameError } from '#lib/utils.ts';
import { IMPORT, wandbImport } from '#lib/wandb.ts';
import { orgHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

const orgPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}`;
const warningPath = (org: string, warning: string) =>
	`${orgPath(org)}/budget/warnings/${encodeURIComponent(warning)}/dismissal`;

export const load: PageServerLoad = async ({ depends, params }) => {
	depends(IMPORT);
	const path = orgPath(params.org);
	// Admin-only reads: members are refused, and the page leaves those sections out.
	const adminOnly = <T>(res: Promise<Response>) =>
		res
			.then((r) => r.json() as Promise<T>)
			.catch((e) => {
				if (isHttpError(e) && e.status === 403) return null;
				throw e;
			});
	const [org, members, me, billing, invites, wandb, deleted, warnings, s3] = await Promise.all([
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
			}),
		adminOnly<{ deleted_projects: DeletedProject[] }>(flock(`${path}/budget/deleted-projects`)),
		adminOnly<BudgetWarnings>(flock(`${path}/budget/warnings`)),
		// The S3 proxy, when the server has one: its key lifetimes are set here (D68).
		authConfig().then((c) => c.s3)
	]);
	return {
		org,
		members: members.members,
		invites: invites?.invites ?? null,
		import: wandb,
		billing,
		deletedProjects: deleted?.deleted_projects ?? null,
		warnings,
		s3,
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

	/** Dismiss a budget warning for everyone in the org, as it was shown. */
	dismissWarning: async ({ params, request }) => {
		const form = await request.formData();
		const warning = formText(form, 'warning');
		if (!warning) return fail(400, { error: 'No such warning' });
		const res = await change('PUT', warningPath(params.org, warning), {
			fingerprint: formText(form, 'fingerprint')
		});
		if (refused(res)) return res;
		return { dismissed: warning };
	},

	/** Show a dismissed budget warning again. */
	restoreWarning: async ({ params, request }) => {
		const warning = formText(await request.formData(), 'warning');
		if (!warning) return fail(400, { error: 'No such warning' });
		const res = await change('DELETE', warningPath(params.org, warning));
		if (refused(res)) return res;
		return { restored: warning };
	},

	/** Leave a deleted project's spend out of the budget (`excluded=true`), or count it again. */
	exclude: async ({ params, request }) => {
		const form = await request.formData();
		const id = Number(form.get('deleted'));
		if (!Number.isInteger(id)) return fail(400, { error: 'No such deleted project' });
		const res = await change<DeletedProject>(
			'PATCH',
			`${orgPath(params.org)}/budget/deleted-projects/${id}`,
			{ excluded: form.get('excluded') === 'true' }
		);
		if (refused(res)) return res;
		return { deleted: res.data };
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

	/** The name people see. The org's ID (its URLs, the wandb entity) never changes. */
	displayName: async ({ params, request }) => {
		const displayName = formText(await request.formData(), 'display_name');
		const invalid = orgDisplayNameError(displayName);
		if (invalid) return fail(400, { error: invalid });
		const res = await change<OrgInfo>('PATCH', orgPath(params.org), {
			display_name: displayName
		});
		if (refused(res)) return res;
		return { org: res.data };
	},

	/** How long S3 keys live: `max` is seconds or `never`; `ephemeral` is seconds. */
	s3Lifetimes: async ({ params, request }) => {
		const form = await request.formData();
		const max = formText(form, 'max');
		const ephemeral = Number(formText(form, 'ephemeral'));
		const maximum = max === 'never' ? null : Number(max);
		if ((maximum !== null && !(maximum > 0)) || !(ephemeral > 0))
			return fail(400, { error: 'Choose both lifetimes' });
		if (maximum !== null && ephemeral > maximum)
			return fail(400, { error: 'Ephemeral keys can’t outlive the maximum' });
		const res = await change<OrgInfo>('PATCH', orgPath(params.org), {
			s3_key_max_ttl: maximum,
			s3_ephemeral_ttl: ephemeral
		});
		if (refused(res)) return res;
		return { org: res.data };
	},

	delete: async ({ locals, params, request }) => {
		const force = (await request.formData()).get('force') === 'on' ? '?force=true' : '';
		const res = await change('DELETE', `${orgPath(params.org)}${force}`);
		if (refused(res)) return res;
		await refreshUser();
		redirect(303, locals.user?.is_superadmin ? '/admin' : '/');
	}
};
