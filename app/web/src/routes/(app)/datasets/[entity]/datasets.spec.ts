import { beforeEach, describe, expect, it, vi } from 'vitest';
import { error, fail, isActionFailure, isHttpError } from '@sveltejs/kit';
import { change, flock } from '#lib/server/flock.ts';
import { actions, load } from './+page.server.ts';
import { actions as datasetActions, load as datasetLoad } from './[dataset]/+page.server.ts';

vi.mock('#lib/server/flock.ts', () => ({
	flock: vi.fn(),
	change: vi.fn(),
	refused: (res: unknown) => isActionFailure(res),
	hubOff: (e: unknown) => isHttpError(e) && e.status === 503,
	datasetPath: (entity: string, dataset: string) => `/api/v1/datasets/${entity}/${dataset}`
}));

const shell = (orgs: string[], is_superadmin = false) => ({
	me: { id: 1, is_superadmin },
	orgs: orgs.map((name) => ({ name }))
});

const listEvent = (entity: string, orgs: string[]) =>
	({ params: { entity }, parent: async () => ({ shell: shell(orgs) }) }) as unknown as Parameters<
		typeof load
	>[0];

const submission = (params: Record<string, string>, fields: Record<string, string> = {}) =>
	({
		params,
		request: new Request('https://flock.test/datasets', {
			method: 'POST',
			body: new URLSearchParams(fields)
		})
	}) as unknown as Parameters<NonNullable<typeof actions.create>>[0];

const json = (body: unknown) => new Response(JSON.stringify(body));
const hubOff = () => {
	try {
		error(503, 'the hub is not configured on this server (FLOCK_HUB_S3_ENDPOINT)');
	} catch (e) {
		return e;
	}
};

beforeEach(() => vi.resetAllMocks());

describe('datasets list', () => {
	it('lists the most recently changed first, and knows members', async () => {
		vi.mocked(flock).mockResolvedValue(
			json({
				datasets: [
					{ name: 'old', updated_at: 1 },
					{ name: 'new', updated_at: 2 }
				]
			})
		);
		const data = await load(listEvent('flock', ['flock']));
		expect(flock).toHaveBeenCalledWith('/api/v1/datasets/flock');
		expect(data).toMatchObject({
			hub: true,
			member: true,
			datasets: [{ name: 'new' }, { name: 'old' }]
		});
	});

	it('shows outsiders the internal datasets, without creating', async () => {
		vi.mocked(flock).mockResolvedValue(json({ datasets: [{ name: 'mmlu', updated_at: 1 }] }));
		await expect(load(listEvent('hf', ['flock']))).resolves.toMatchObject({ member: false });
	});

	it('says so calmly while the hub is off', async () => {
		vi.mocked(flock).mockRejectedValue(hubOff());
		await expect(load(listEvent('flock', ['flock']))).resolves.toMatchObject({
			hub: false,
			datasets: []
		});
	});

	it('creates a dataset and opens it', async () => {
		vi.mocked(change).mockResolvedValue({ data: { name: 'fineweb' } });
		await expect(
			actions.create(
				submission(
					{ entity: 'flock' },
					{ name: 'fineweb', description: 'web text', visibility: 'internal' }
				)
			)
		).rejects.toMatchObject({ status: 303, location: '/datasets/flock/fineweb' });
		expect(change).toHaveBeenCalledWith('POST', '/api/v1/datasets/flock', {
			name: 'fineweb',
			description: 'web text',
			visibility: 'internal'
		});
	});

	it('refuses invalid names before asking Flock', async () => {
		const result = await actions.create(submission({ entity: 'flock' }, { name: 'a/b' }));
		expect(result).toMatchObject({ status: 400 });
		expect(change).not.toHaveBeenCalled();
	});
});

const datasetEvent = (v: string | null = null) =>
	({
		params: { entity: 'flock', dataset: 'fineweb' },
		url: new URL(`https://flock.test/datasets/flock/fineweb${v ? `?v=${v}` : ''}`)
	}) as unknown as Parameters<typeof datasetLoad>[0];

const at = (fields: Record<string, string> = {}) =>
	submission({ entity: 'flock', dataset: 'fineweb' }, fields) as unknown as Parameters<
		NonNullable<typeof datasetActions.tag>
	>[0];

describe('dataset page', () => {
	function answer(latest: number | null) {
		vi.mocked(flock).mockImplementation(async (path: string) => {
			if (path.endsWith('/versions?limit=500')) return json({ versions: [], total: 0 });
			if (path.includes('/readme')) return json({ version: latest, sha: null, content: null });
			if (path === '/api/v1/hub/credentials')
				return json({
					endpoint: 'https://hub.flock.test',
					region: 'us-east-1',
					secret_access_key: 'x'
				});
			return json({ name: 'fineweb', latest_version: latest });
		});
	}

	it('loads the latest version, and only the endpoint of the credentials', async () => {
		answer(4);
		const data = await datasetLoad(datasetEvent());
		expect(data).toMatchObject({ hub: true, version: 4, pinned: false });
		expect((data as { s3: unknown }).s3).toEqual({
			endpoint: 'https://hub.flock.test',
			region: 'us-east-1'
		});
	});

	it('pins an earlier version from ?v=', async () => {
		answer(4);
		await expect(datasetLoad(datasetEvent('2'))).resolves.toMatchObject({
			version: 2,
			pinned: true
		});
		expect(flock).toHaveBeenCalledWith('/api/v1/datasets/flock/fineweb/readme?version=2');
	});

	it('says so calmly while the hub is off', async () => {
		vi.mocked(flock).mockRejectedValue(hubOff());
		await expect(datasetLoad(datasetEvent())).resolves.toEqual({ hub: false });
	});

	it('tags a version', async () => {
		vi.mocked(change).mockResolvedValue({ data: { tags: { 'v1.0': 3 } } });
		await expect(datasetActions.tag(at({ tag: 'v1.0', version: '3' }))).resolves.toEqual({
			tag: 'v1.0',
			version: 3
		});
		expect(change).toHaveBeenCalledWith('PUT', '/api/v1/datasets/flock/fineweb/tags/v1.0', {
			version: 3
		});
	});

	it('refuses tag names Lance can’t keep', async () => {
		await expect(datasetActions.tag(at({ tag: '../x', version: '3' }))).resolves.toMatchObject({
			status: 400
		});
		expect(change).not.toHaveBeenCalled();
	});

	it('prunes by age in days', async () => {
		vi.mocked(change).mockResolvedValue({ data: { versions_removed: 2, bytes_removed: 4096 } });
		await expect(datasetActions.prune(at({ days: '7.5' }))).resolves.toEqual({
			pruned: 2,
			bytes: 4096
		});
		expect(change).toHaveBeenCalledWith('POST', '/api/v1/datasets/flock/fineweb/prune', {
			older_than_days: 7.5
		});
		await expect(datasetActions.prune(at({ days: '-1' }))).resolves.toMatchObject({ status: 400 });
	});

	it('restores with an optional message', async () => {
		vi.mocked(change).mockResolvedValue({ data: { version: 9 } });
		await expect(datasetActions.restore(at({ version: '2', message: '' }))).resolves.toEqual({
			restored: 2,
			version: 9
		});
		expect(change).toHaveBeenCalledWith('POST', '/api/v1/datasets/flock/fineweb/restore', {
			version: 2
		});
	});

	it('follows a rename to the new URL', async () => {
		vi.mocked(change).mockResolvedValue({ data: {} });
		await expect(datasetActions.rename(at({ name: 'fineweb-2' }))).rejects.toMatchObject({
			status: 303,
			location: '/datasets/flock/fineweb-2?tab=settings'
		});
	});

	it('goes back to the list after a delete', async () => {
		vi.mocked(change).mockResolvedValue({ data: null });
		await expect(datasetActions.delete(at())).rejects.toMatchObject({
			status: 303,
			location: '/datasets/flock'
		});
		expect(change).toHaveBeenCalledWith('DELETE', '/api/v1/datasets/flock/fineweb');
	});

	it('keeps Flock’s refusals for the form', async () => {
		const refusal = fail(403, { error: 'only admins of flock can do this' });
		vi.mocked(change).mockResolvedValue(refusal);
		await expect(datasetActions.visibility(at({ visibility: 'internal' }))).resolves.toBe(refusal);
	});
});
