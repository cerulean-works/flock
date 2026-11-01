import { fail } from '@sveltejs/kit';
import { authConfig, refreshUser } from '#lib/server/auth.ts';
import { removeAvatar, setAvatar } from '#lib/server/avatar.ts';
import { change, refused, flock } from '#lib/server/flock.ts';
import type {
	CliSession,
	OrgInfo,
	S3Key,
	S3KeyCreated,
	UserInfo,
	WandbKey,
	WandbKeyCreated
} from '#lib/types.ts';
import { formText } from '#lib/utils.ts';
import type { Actions, PageServerLoad } from './$types';

/** The user's S3 keys and the key lifetimes of their orgs, when the server has S3. */
async function s3Keys() {
	const { s3 } = await authConfig();
	if (!s3) return null;
	const [keys, orgs] = await Promise.all([
		flock('/api/v1/me/s3-keys').then((r) => r.json() as Promise<{ keys: S3Key[] }>),
		flock('/api/v1/orgs').then((r) => r.json() as Promise<{ orgs: OrgInfo[] }>)
	]);
	return {
		...s3,
		keys: keys.keys,
		orgs: orgs.orgs.map(({ name, display_name, s3_key_max_ttl }) => ({
			name,
			display_name,
			s3_key_max_ttl
		}))
	};
}

export const load: PageServerLoad = async () => {
	const [me, keys, cli, s3] = await Promise.all([
		flock('/api/v1/me'),
		flock('/api/v1/me/wandb-keys'),
		flock('/api/v1/me/cli-sessions'),
		s3Keys()
	]);
	return {
		profile: (await me.json()) as UserInfo,
		keys: ((await keys.json()) as { keys: WandbKey[] }).keys,
		cliSessions: ((await cli.json()) as { sessions: CliSession[] }).sessions,
		s3,
		now: Date.now() / 1000
	};
};

export const actions: Actions = {
	avatar: ({ request }) => setAvatar(request, '/api/v1/me'),
	removeAvatar: () => removeAvatar('/api/v1/me'),

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

	/** A new wandb key. Flock shows it this once; it keeps only a hash. */
	createKey: async ({ request }) => {
		const form = await request.formData();
		const name = formText(form, 'name') || 'default';
		const res = await change<WandbKeyCreated>('POST', '/api/v1/me/wandb-keys', { name });
		if (refused(res)) return res;
		return { key: res.data.key, name: res.data.name };
	},

	deleteKey: async ({ request }) => {
		const form = await request.formData();
		const id = Number(form.get('id'));
		if (!Number.isInteger(id)) return fail(400, { error: 'No such key' });
		const res = await change('DELETE', `/api/v1/me/wandb-keys/${id}`);
		if (refused(res)) return res;
		return { deleted: id };
	},

	/**
	 * A long-lived S3 key. Its secret is shown this once: Flock derives it from the key's id
	 * and stores neither. `ttl` is seconds, or `never`.
	 */
	createS3Key: async ({ request }) => {
		const form = await request.formData();
		const name = formText(form, 'name');
		const org = formText(form, 'org');
		const ttl = formText(form, 'ttl');
		if (!name) return fail(400, { error: 'Name the key' });
		if (!org) return fail(400, { error: 'Choose an organization' });
		const seconds = ttl === 'never' ? null : Number(ttl);
		if (seconds !== null && !(seconds > 0)) return fail(400, { error: 'Choose when it expires' });
		const res = await change<S3KeyCreated>('POST', '/api/v1/me/s3-keys', {
			name,
			org,
			read_only: form.get('read_only') === 'on',
			ttl: seconds
		});
		if (refused(res)) return res;
		return { s3Key: res.data };
	},

	/** Revoke S3 keys (all of a machine's ephemeral keys at once, or one). */
	deleteS3Keys: async ({ request }) => {
		const ids = (await request.formData()).getAll('id').map(Number);
		if (!ids.length || !ids.every(Number.isInteger)) return fail(400, { error: 'No such key' });
		for (const id of ids) {
			const res = await change('DELETE', `/api/v1/me/s3-keys/${id}`);
			// Already gone (expired and swept meanwhile) is as good as revoked.
			if (refused(res) && res.status !== 404) return res;
		}
		return { revokedS3: ids.length };
	},

	/** Sign a machine out of `flock login`: its token stops working on its next request. */
	revokeCli: async ({ request }) => {
		const form = await request.formData();
		const id = Number(form.get('id'));
		if (!Number.isInteger(id)) return fail(400, { error: 'No such sign-in' });
		const res = await change('DELETE', `/api/v1/me/cli-sessions/${id}`);
		if (refused(res)) return res;
		return { revoked: id };
	}
};
