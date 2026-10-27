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
