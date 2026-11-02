import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { RequestEvent } from '@sveltejs/kit';
import { resolveSession } from '#lib/server/session.ts';
import { handle } from './hooks.server.ts';

vi.mock('$app/env/private', () => ({ FLOCK_API_URL: 'http://api.flock.test' }));
vi.mock('$app/manifest', () => ({ immutable: [] }));
vi.mock('#lib/server/session.ts', async (original) => ({
	...(await original<typeof import('#lib/server/session.ts')>()),
	resolveSession: vi.fn()
}));

/** A request for `path`, signed in or not, that the app answers with `status`. */
async function request(
	path: string,
	{ token = null as string | null, status = 200, method = 'GET' } = {}
) {
	vi.mocked(resolveSession).mockResolvedValue({ token, user: null });
	const resolve = vi.fn(async () => new Response('page', { status }));
	const event = {
		url: new URL(`https://flock.test${path}`),
		cookies: { get: () => undefined },
		locals: {},
		isDataRequest: false,
		request: new Request(`https://flock.test${path}`, { method })
	} as unknown as RequestEvent;
	const res = await handle({ event, resolve } as unknown as Parameters<typeof handle>[0]);
	return { res, resolved: resolve.mock.calls.length > 0 };
}

beforeEach(() => vi.clearAllMocks());

describe('signed-out visitors', () => {
	it.each([
		'/acme',
		'/acme/bert',
		'/acme/bert/runs/abc123',
		'/datasets/acme/eval/mmlu',
		'/api/projects/acme/bert/runs',
		'/api/projects/acme/bert/live',
		'/api/datasets/acme/rows'
	])('open %s, which may be public', async (path) => {
		const { res, resolved } = await request(path);
		expect(resolved).toBe(true);
		expect(res.status).toBe(200);
	});

	it.each(['/', '/account', '/admin', '/orgs/acme', '/storage/acme', '/models/acme', '/device'])(
		'sign in before %s',
		async (path) => {
			const { res, resolved } = await request(path);
			expect(resolved).toBe(false);
			expect(res.status).toBe(303);
			expect(res.headers.get('location')).toMatch(/^\/login/);
		}
	);

	it('get a 401 from this server’s other endpoints', async () => {
		const { res } = await request('/api/orgs/acme/members');
		expect(res.status).toBe(401);
	});

	it('get a 401 for writes through the readable endpoints', async () => {
		const { res, resolved } = await request('/api/datasets/acme/delete', { method: 'POST' });
		expect(resolved).toBe(false);
		expect(res.status).toBe(401);
	});

	it('go to sign in from a page that isn’t there, coming back to it', async () => {
		const { res } = await request('/acme/bert/runs/abc123/nope', { status: 404 });
		expect(res.status).toBe(303);
		expect(res.headers.get('location')).toBe('/login?next=%2Facme%2Fbert%2Fruns%2Fabc123%2Fnope');
	});
});

describe('signed-in users', () => {
	it('see the 404 page', async () => {
		const { res } = await request('/acme/bert/runs/abc123/nope', { token: 'jwt', status: 404 });
		expect(res.status).toBe(404);
	});
});
