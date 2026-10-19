import { fail } from '@sveltejs/kit';
import { removeAvatar, setAvatar, useGravatar } from '#lib/server/avatar.ts';
import { change, refused, flock } from '#lib/server/flock.ts';
import type { UserInfo } from '#lib/types.ts';
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
	const [me, keys] = await Promise.all([flock('/api/v1/me'), flock('/api/v1/me/api-keys')]);
	return {
		profile: (await me.json()) as UserInfo,
		keys: ((await keys.json()) as { keys: ApiKey[] }).keys,
		now: Date.now() / 1000
	};
};

export const actions: Actions = {
	avatar: ({ request }) => setAvatar(request, '/api/v1/me'),
	removeAvatar: () => removeAvatar('/api/v1/me'),
	gravatar: () => useGravatar(),

	/** A new API key. Flock shows it this once; it keeps only a hash. */
	createKey: async ({ request }) => {
		const form = await request.formData();
		const name = formText(form, 'name') || 'default';
		const res = await change<ApiKey & { key: string }>('POST', '/api/v1/me/api-keys', { name });
		if (refused(res)) return res;
		return { key: res.data.key, name: res.data.name };
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
