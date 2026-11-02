import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flock } from './flock.ts';

vi.mock('$app/env/private', () => ({ FLOCK_API_URL: 'http://api.flock.test' }));

/** The request being answered: a page (or an endpoint of this server), signed in or not. */
const event = vi.hoisted(() => ({
	locals: { token: null as string | null },
	url: new URL('https://flock.test/acme/bert'),
	cookies: { delete: () => {} }
}));
vi.mock('$app/server', () => ({ getRequestEvent: () => event }));

function answer(status: number, body: unknown = {}) {
	const fetch = vi.fn(async () => Response.json(body, { status }));
	vi.stubGlobal('fetch', fetch);
	return fetch;
}

const visit = (path: string, token: string | null) => {
	event.url = new URL(`https://flock.test${path}`);
	event.locals.token = token;
};

beforeEach(() => visit('/acme/bert', null));
afterEach(() => vi.unstubAllGlobals());

describe('reading from Flock signed out', () => {
	it('sends no token', async () => {
		const fetch = answer(200, { name: 'bert' });
		await flock('/api/v1/projects/acme/bert');
		const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(new Headers(init.headers).has('authorization')).toBe(false);
	});

	it('sends the session’s token when signed in', async () => {
		visit('/acme/bert', 'jwt');
		const fetch = answer(200, {});
		await flock('/api/v1/projects/acme/bert');
		const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(new Headers(init.headers).get('authorization')).toBe('Bearer jwt');
	});

	it('goes to sign in on a 404, coming back to the page: it may be private', async () => {
		visit('/acme/secret?view=charts', null);
		answer(404, { error: 'project not found' });
		await expect(flock('/api/v1/projects/acme/secret')).rejects.toMatchObject({
			status: 303,
			location: '/login?next=%2Facme%2Fsecret%3Fview%3Dcharts'
		});
	});

	it('shows the 404 page when signed in', async () => {
		visit('/acme/secret', 'jwt');
		answer(404, { error: 'project not found' });
		await expect(flock('/api/v1/projects/acme/secret')).rejects.toMatchObject({
			status: 404,
			body: { message: 'project not found' }
		});
	});

	it('answers 404 from this server’s own endpoints, signed out too', async () => {
		visit('/api/projects/acme/secret', null);
		answer(404, { error: 'project not found' });
		await expect(flock('/api/v1/projects/acme/secret')).rejects.toMatchObject({ status: 404 });
	});
});
