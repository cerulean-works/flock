import { fail } from '@sveltejs/kit';
import { avatarFileError } from '#lib/utils.ts';
import { change, refused } from './flock.ts';

/**
 * Avatar changes for a user (`/api/v1/me`) or an organization (`/api/v1/orgs/{org}`), from a
 * form with an `avatar` file. Flock takes the image's bytes as the request body.
 */
export async function setAvatar(request: Request, owner: string) {
	const file = (await request.formData()).get('avatar');
	const invalid = avatarFileError(file);
	if (invalid) return fail(400, { error: invalid });
	const res = await change('PUT', `${owner}/avatar`, file as File);
	if (refused(res)) return res;
	return { avatar: 'set' };
}

export async function removeAvatar(owner: string) {
	const res = await change('DELETE', `${owner}/avatar`);
	if (refused(res)) return res;
	return { avatar: 'removed' };
}

/** The user's picture from Gravatar, fetched again now. */
export async function useGravatar() {
	const res = await change('POST', '/api/v1/me/avatar/gravatar');
	if (refused(res)) {
		if (res.status === 404) return fail(404, { error: 'Gravatar has no picture for your email' });
		if (res.status === 409) return fail(409, { error: 'Gravatar is turned off on this server' });
		return res;
	}
	return { avatar: 'gravatar' };
}
