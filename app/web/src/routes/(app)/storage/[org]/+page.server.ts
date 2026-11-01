import { error, fail } from '@sveltejs/kit';
import { authConfig } from '#lib/server/auth.ts';
import { change, flock, refused } from '#lib/server/flock.ts';
import { bucketNameError } from '#lib/s3.ts';
import type { Bucket } from '#lib/types.ts';
import { formText } from '#lib/utils.ts';
import type { Actions, PageServerLoad } from './$types';

const bucketsPath = (org: string) => `/api/v1/orgs/${encodeURIComponent(org)}/buckets`;

/** The org's buckets (D64), those still being emptied after a delete among them. */
export const load: PageServerLoad = async ({ params }) => {
	const [{ s3 }, listed] = await Promise.all([
		authConfig(),
		flock(`${bucketsPath(params.org)}?deleting=true`)
			.then((r) => r.json() as Promise<{ buckets: Bucket[]; name_limit: number }>)
			.catch((e) => {
				// Without S3 the route is 404 too: say why rather than "not found".
				if (e?.status === 404) return null;
				throw e;
			})
	]);
	if (!s3) error(404, 'This Flock server has no S3 storage');
	if (!listed) error(404, `Organization ${params.org} not found`);
	return {
		s3,
		buckets: listed.buckets,
		nameLimit: listed.name_limit,
		now: Date.now() / 1000
	};
};

export const actions: Actions = {
	/** A new bucket: any member can make one. */
	createBucket: async ({ params, request }) => {
		const form = await request.formData();
		const name = formText(form, 'name');
		const invalid = bucketNameError(name, Number(form.get('limit')) || 57);
		if (invalid) return fail(400, { error: invalid });
		const res = await change('POST', bucketsPath(params.org), { name });
		if (refused(res)) return res;
		return { bucket: name };
	},

	/** Delete a bucket and everything in it (admins). Flock empties it in the background. */
	deleteBucket: async ({ params, request }) => {
		const name = formText(await request.formData(), 'bucket');
		if (!name) return fail(400, { error: 'No such bucket' });
		const res = await change('DELETE', `${bucketsPath(params.org)}/${encodeURIComponent(name)}`);
		if (refused(res)) return res;
		return { deletedBucket: name };
	}
};
