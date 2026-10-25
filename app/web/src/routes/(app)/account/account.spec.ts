import { beforeEach, describe, expect, it, vi } from 'vitest';
import { error, isActionFailure, isHttpError } from '@sveltejs/kit';
import { change, flock } from '#lib/server/flock.ts';
import { actions, load } from './+page.server.ts';

vi.mock('#lib/server/flock.ts', () => ({
	flock: vi.fn(),
	change: vi.fn(),
	refused: (res: unknown) => isActionFailure(res),
	hubOff: (e: unknown) => isHttpError(e) && e.status === 503
}));
vi.mock('#lib/server/auth.ts', () => ({ refreshUser: vi.fn() }));
vi.mock('#lib/server/avatar.ts', () => ({
	removeAvatar: vi.fn(),
	setAvatar: vi.fn(),
	useGravatar: vi.fn()
}));

const json = (body: unknown) => new Response(JSON.stringify(body));

const submission = (fields: Record<string, string>) =>
	({
		request: new Request('https://flock.test/account', {
			method: 'POST',
			body: new URLSearchParams(fields)
		})
	}) as unknown as Parameters<NonNullable<typeof actions.createHubKey>>[0];

beforeEach(() => vi.resetAllMocks());

describe('S3 keys', () => {
	it('lists them with the API keys', async () => {
		vi.mocked(flock).mockImplementation(async (path: string) =>
			json(path === '/api/v1/me/hub-keys' ? { keys: [{ pk: 1 }] } : { keys: [] })
		);
		await expect(load({} as Parameters<typeof load>[0])).resolves.toMatchObject({
			hubKeys: [{ pk: 1 }]
		});
	});

	it('leaves them out while the hub is off', async () => {
		vi.mocked(flock).mockImplementation(async (path: string) => {
			if (path === '/api/v1/me/hub-keys') error(503, 'the hub is not configured');
			return json({ keys: [] });
		});
		await expect(load({} as Parameters<typeof load>[0])).resolves.toMatchObject({ hubKeys: null });
	});

	it('creates a read-only key for one organization and returns its secret once', async () => {
		const created = { pk: 2, name: 'cluster', secret_access_key: 's' };
		vi.mocked(change).mockResolvedValue({ data: created });
		await expect(
			actions.createHubKey(submission({ name: 'cluster', org: 'flock', read_only: 'on' }))
		).resolves.toEqual({ created });
		expect(change).toHaveBeenCalledWith('POST', '/api/v1/me/hub-keys', {
			name: 'cluster',
			read_only: true,
			org: 'flock'
		});
	});

	it('creates a key for every organization', async () => {
		vi.mocked(change).mockResolvedValue({ data: {} });
		await actions.createHubKey(submission({ name: 'laptop', org: '/' }));
		expect(change).toHaveBeenCalledWith('POST', '/api/v1/me/hub-keys', {
			name: 'laptop',
			read_only: false
		});
	});

	it('asks for a name', async () => {
		await expect(actions.createHubKey(submission({ name: ' ' }))).resolves.toMatchObject({
			status: 400
		});
		expect(change).not.toHaveBeenCalled();
	});

	it('deletes a key', async () => {
		vi.mocked(change).mockResolvedValue({ data: null });
		await expect(actions.deleteHubKey(submission({ pk: '2' }))).resolves.toEqual({
			deletedHubKey: 2
		});
		expect(change).toHaveBeenCalledWith('DELETE', '/api/v1/me/hub-keys/2');
	});
});
