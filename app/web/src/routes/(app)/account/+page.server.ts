import { fail } from '@sveltejs/kit';
import { refreshUser } from '#lib/server/auth.ts';
import { removeAvatar, setAvatar, useGravatar } from '#lib/server/avatar.ts';
import { change, refused, flock, hubOff } from '#lib/server/flock.ts';
import type { CreatedHubKey, HubKey, UserInfo } from '#lib/types.ts';
import { formText } from '#lib/utils.ts';
import type { Actions, PageServerLoad } from './$types';

export interface ApiKey {
	id: number;
	name: string;
	/** The key's first characters, to tell keys apart. */
	prefix: string;
	created_at: number;
}

export const load: PageServerLoad = async () => {
	const [me, keys, hubKeys] = await Promise.all([
		flock('/api/v1/me'),
		flock('/api/v1/me/api-keys'),
		// S3 keys for the hub's datasets; null while the hub isn't configured.
		flock('/api/v1/me/hub-keys')
			.then((r) => r.json() as Promise<{ keys: HubKey[] }>)
			.then(({ keys }) => keys)
			.catch((e) => {
				if (hubOff(e)) return null;
				throw e;
			})
	]);
	return {
		profile: (await me.json()) as UserInfo,
		keys: ((await keys.json()) as { keys: ApiKey[] }).keys,
		hubKeys,
		now: Date.now() / 1000
	};
};

export const actions: Actions = {
	avatar: ({ request }) => setAvatar(request, '/api/v1/me'),
	removeAvatar: () => removeAvatar('/api/v1/me'),
	gravatar: () => useGravatar(),

	/** The name others see on runs, feeds and member lists. */
	profile: async ({ request }) => {
		const name = formText(await request.formData(), 'name');
		if (!name) return fail(400, { error: 'Enter your name' });
		if (name.length > 128) return fail(400, { error: 'Use at most 128 characters' });
		const res = await change('PATCH', '/api/v1/me', { name });
		if (refused(res)) return res;
		await refreshUser();
		return { name };
	},

	/**
	 * Set or change the password. Flock ends the user's other sessions; this one continues.
	 * Accounts made with Google have none yet, and need no current one.
	 */
	password: async ({ request }) => {
		const form = await request.formData();
		const current = String(form.get('current') ?? '');
		const next = String(form.get('new') ?? '');
		if (next.length < 8) return fail(400, { error: 'Use at least 8 characters' });
		if (next !== String(form.get('confirm') ?? ''))
			return fail(400, { error: 'The new passwords don’t match' });
		const res = await change('POST', '/api/v1/me/password', {
			new: next,
			...(current && { current })
		});
		if (refused(res)) return res;
		return { password: true };
	},

	/** The organization `wandb.init` logs to when it names no entity. */
	defaultOrg: async ({ request }) => {
		const org = formText(await request.formData(), 'org');
		if (!org) return fail(400, { error: 'Choose an organization' });
		const res = await change('PATCH', '/api/v1/me', { default_org: org });
		if (refused(res)) return res;
		await refreshUser();
		return { org };
	},

	/** A new API key. Flock shows it this once; it keeps only a hash. */
	createKey: async ({ request }) => {
		const form = await request.formData();
		const name = formText(form, 'name') || 'default';
		const res = await change<ApiKey & { key: string }>('POST', '/api/v1/me/api-keys', { name });
		if (refused(res)) return res;
		return { key: res.data.key, name: res.data.name };
	},

	/**
	 * A new S3 key, read-only or not, for every org the user is in or one of them (`org`; `/` or
	 * empty for every one). Flock shows its secret this once.
	 */
	createHubKey: async ({ request }) => {
		const form = await request.formData();
		const name = formText(form, 'name');
		if (!name) return fail(400, { error: 'Name the key' });
		if (name.length > 128) return fail(400, { error: 'Use at most 128 characters' });
		const org = formText(form, 'org');
		const res = await change<CreatedHubKey>('POST', '/api/v1/me/hub-keys', {
			name,
			read_only: form.get('read_only') === 'on',
			...(org && org !== '/' && { org })
		});
		if (refused(res)) return res;
		return { created: res.data };
	},

	deleteHubKey: async ({ request }) => {
		const pk = Number((await request.formData()).get('pk'));
		if (!Number.isInteger(pk)) return fail(400, { error: 'No such key' });
		const res = await change('DELETE', `/api/v1/me/hub-keys/${pk}`);
		if (refused(res)) return res;
		return { deletedHubKey: pk };
	},

	deleteKey: async ({ request }) => {
		const form = await request.formData();
		const id = Number(form.get('id'));
		if (!Number.isInteger(id)) return fail(400, { error: 'No such key' });
		const res = await change('DELETE', `/api/v1/me/api-keys/${id}`);
		if (refused(res)) return res;
		return { deleted: id };
	}
};
