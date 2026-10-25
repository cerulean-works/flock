import { fail, redirect } from '@sveltejs/kit';
import { change, flock, hubOff, refused } from '#lib/server/flock.ts';
import type { DatasetInfo, DatasetVisibility } from '#lib/types.ts';
import { formText, projectNameError } from '#lib/utils.ts';
import { datasetHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/**
 * An organization's datasets, most recently changed first: all of them for its members, the
 * internal ones for everyone else. `hub: false` while the hub isn't configured on this server.
 */
export const load: PageServerLoad = async ({ params, parent }) => {
	const { shell } = await parent();
	const member = shell.me.is_superadmin || shell.orgs.some((o) => o.name === params.entity);
	let datasets: DatasetInfo[];
	try {
		const res = await flock(`/api/v1/datasets/${encodeURIComponent(params.entity)}`);
		datasets = ((await res.json()) as { datasets: DatasetInfo[] }).datasets;
	} catch (e) {
		if (hubOff(e)) return { hub: false as const, member, datasets: [], now: Date.now() / 1000 };
		throw e;
	}
	return {
		hub: true as const,
		member,
		datasets: datasets.toSorted((a, b) => b.updated_at - a.updated_at),
		now: Date.now() / 1000
	};
};

const isVisibility = (v: unknown): v is DatasetVisibility => v === 'private' || v === 'internal';

export const actions: Actions = {
	/** A new, empty dataset; its first version is its first write. */
	create: async ({ params, request }) => {
		const form = await request.formData();
		const name = formText(form, 'name');
		const invalid = projectNameError(name);
		if (invalid) return fail(400, { error: invalid });
		const visibility = form.get('visibility') ?? 'private';
		if (!isVisibility(visibility)) return fail(400, { error: 'Choose who can see it' });
		const res = await change<DatasetInfo>(
			'POST',
			`/api/v1/datasets/${encodeURIComponent(params.entity)}`,
			{ name, description: formText(form, 'description'), visibility }
		);
		if (refused(res)) return res;
		redirect(303, datasetHref(params.entity, res.data.name));
	}
};
