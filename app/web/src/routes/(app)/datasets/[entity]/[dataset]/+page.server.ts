import { fail, isHttpError, redirect } from '@sveltejs/kit';
import { change, datasetPath, flock, hubOff, refused } from '#lib/server/flock.ts';
import type { DatasetInfo, DatasetReadme, DatasetVersion, DatasetVisibility } from '#lib/types.ts';
import { formText, projectNameError } from '#lib/utils.ts';
import { datasetHref, datasetsHref } from '#lib/workspace/url.ts';
import type { Actions, PageServerLoad } from './$types';

/** Versions the page lists: Flock serves at most 500 at a time. */
const VERSIONS = 500;

/** For a read the page can do without: its fallback when Flock refuses it (not a sign-in). */
const otherwise =
	<T>(fallback: T) =>
	(e: unknown): T => {
		if (isHttpError(e) && e.status !== 503) return fallback;
		throw e;
	};

/** A version number from a search param or form field, or null. */
function versionOf(raw: unknown): number | null {
	const v = Number(raw);
	return raw != null && raw !== '' && Number.isInteger(v) && v >= 1 ? v : null;
}

/**
 * A dataset: its summary, version history and card at the version in view (`?v=`, the latest
 * without it). Reads only `v`, so switching tabs (`?tab=`) doesn't load it again. The hub's S3
 * endpoint comes from the temporary credentials Flock hands out; only the endpoint and region
 * reach the page.
 */
export const load: PageServerLoad = async ({ params, url }) => {
	const path = datasetPath(params.entity, params.dataset);
	const asked = versionOf(url.searchParams.get('v'));
	try {
		const [dataset, history, readme, s3] = await Promise.all([
			flock(path).then((r) => r.json() as Promise<DatasetInfo>),
			flock(`${path}/versions?limit=${VERSIONS}`).then(
				(r) => r.json() as Promise<{ versions: DatasetVersion[]; total: number }>
			),
			flock(`${path}/readme${asked ? `?version=${asked}` : ''}`)
				.then((r) => r.json() as Promise<DatasetReadme>)
				// A pruned or unknown version has no card to show.
				.catch(otherwise<DatasetReadme>({ version: asked, sha: null, content: null })),
			flock('/api/v1/hub/credentials', {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ read_only: true })
			})
				.then((r) => r.json() as Promise<{ endpoint: string | null; region: string }>)
				.then(({ endpoint, region }) => ({ endpoint, region }))
				.catch(otherwise({ endpoint: null as string | null, region: 'us-east-1' }))
		]);
		return {
			hub: true as const,
			dataset,
			versions: history.versions,
			totalVersions: history.total,
			/** The version in view: asked for, else the latest (null before the first write). */
			version: asked ?? dataset.latest_version,
			pinned: asked != null && asked !== dataset.latest_version,
			readme,
			s3,
			now: Date.now() / 1000
		};
	} catch (e) {
		if (hubOff(e)) return { hub: false as const };
		throw e;
	}
};

const isVisibility = (v: unknown): v is DatasetVisibility => v === 'private' || v === 'internal';

/** Lance's tag names (Flock checks them too). */
const TAG = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export const actions: Actions = {
	/** Replace the card: a new version. */
	readme: async ({ params, request }) => {
		const form = await request.formData();
		const content = String(form.get('content') ?? '');
		const message = formText(form, 'message');
		const res = await change<{ version: number }>(
			'PUT',
			`${datasetPath(params.entity, params.dataset)}/readme`,
			{ content, ...(message && { message }) }
		);
		if (refused(res)) return res;
		return { version: res.data.version };
	},

	/** The description (members). */
	describe: async ({ params, request }) => {
		const description = formText(await request.formData(), 'description');
		const res = await change('PATCH', datasetPath(params.entity, params.dataset), {
			description
		});
		if (refused(res)) return res;
		return { described: true };
	},

	/** A new name (admins): clients address it by name, so its URI moves with it. */
	rename: async ({ params, request }) => {
		const name = formText(await request.formData(), 'name');
		const invalid = projectNameError(name);
		if (invalid) return fail(400, { error: invalid });
		if (name === params.dataset) return { renamed: name };
		const res = await change('PATCH', datasetPath(params.entity, params.dataset), { name });
		if (refused(res)) return res;
		redirect(303, datasetHref(params.entity, name, { tab: 'settings' }));
	},

	/** Who can see it (admins). */
	visibility: async ({ params, request }) => {
		const visibility = (await request.formData()).get('visibility');
		if (!isVisibility(visibility)) return fail(400, { error: 'Choose who can see it' });
		const res = await change('PATCH', datasetPath(params.entity, params.dataset), { visibility });
		if (refused(res)) return res;
		return { visibility };
	},

	/** Delete it with every version (admins). There is no undo. */
	delete: async ({ params }) => {
		const res = await change('DELETE', datasetPath(params.entity, params.dataset));
		if (refused(res)) return res;
		redirect(303, datasetsHref(params.entity));
	},

	/** Point a tag at a version, creating or moving it. */
	tag: async ({ params, request }) => {
		const form = await request.formData();
		const tag = formText(form, 'tag');
		const version = versionOf(form.get('version'));
		if (!TAG.test(tag))
			return fail(400, {
				error: 'Use letters, digits, dots, dashes and underscores, starting with a letter or digit'
			});
		if (version == null) return fail(400, { error: 'No such version' });
		const res = await change(
			'PUT',
			`${datasetPath(params.entity, params.dataset)}/tags/${encodeURIComponent(tag)}`,
			{ version }
		);
		if (refused(res)) return res;
		return { tag, version };
	},

	untag: async ({ params, request }) => {
		const tag = formText(await request.formData(), 'tag');
		if (!TAG.test(tag)) return fail(400, { error: 'No such tag' });
		const res = await change(
			'DELETE',
			`${datasetPath(params.entity, params.dataset)}/tags/${encodeURIComponent(tag)}`
		);
		if (refused(res)) return res;
		return { untagged: tag };
	},

	/** Make an earlier version the latest again, as a new version. */
	restore: async ({ params, request }) => {
		const form = await request.formData();
		const version = versionOf(form.get('version'));
		if (version == null) return fail(400, { error: 'No such version' });
		const message = formText(form, 'message');
		const res = await change<{ version: number }>(
			'POST',
			`${datasetPath(params.entity, params.dataset)}/restore`,
			{ version, ...(message && { message }) }
		);
		if (refused(res)) return res;
		return { restored: version, version: res.data.version };
	},

	/** Compact small fragments into a new version (none when there is nothing to compact). */
	optimize: async ({ params }) => {
		const res = await change<{ version: number }>(
			'POST',
			`${datasetPath(params.entity, params.dataset)}/optimize`
		);
		if (refused(res)) return res;
		return { optimized: res.data.version };
	},

	/** Delete old versions' files (admins); the latest and tagged versions stay. */
	prune: async ({ params, request }) => {
		const days = Number(formText(await request.formData(), 'days'));
		if (!Number.isFinite(days) || days < 0)
			return fail(400, { error: 'Enter a number of days, 0 or more' });
		const res = await change<{ versions_removed: number; bytes_removed: number }>(
			'POST',
			`${datasetPath(params.entity, params.dataset)}/prune`,
			{ older_than_days: days }
		);
		if (refused(res)) return res;
		return { pruned: res.data.versions_removed, bytes: res.data.bytes_removed };
	},

	/** Describe a version committed without a message (its author, or an admin; once). */
	message: async ({ params, request }) => {
		const form = await request.formData();
		const version = versionOf(form.get('version'));
		const message = formText(form, 'message');
		if (version == null) return fail(400, { error: 'No such version' });
		if (!message) return fail(400, { error: 'Enter a message' });
		const res = await change(
			'PATCH',
			`${datasetPath(params.entity, params.dataset)}/versions/${version}`,
			{ message }
		);
		if (refused(res)) return res;
		return { described: version };
	}
};
