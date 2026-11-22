import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flockAnswer } from '#lib/server/flock.ts';
import { DELETE, GET, POST } from './+server.ts';

vi.mock('#lib/server/flock.ts', () => ({ flockAnswer: vi.fn() }));

beforeEach(() => vi.resetAllMocks());

/** A browser call to `/api/evals/acme/{op}`, as SvelteKit hands it to the handler. */
function call(handler: typeof GET, method: string, op: string, body?: unknown) {
	const request = new Request(`http://flock.test/api/evals/acme/${op}`, {
		method,
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	return handler({ params: { org: 'acme', op }, request } as Parameters<typeof GET>[0]);
}

describe('relaying eval launches', () => {
	it('reaches the catalogue, the list and one launch, each with its own methods', async () => {
		vi.mocked(flockAnswer).mockImplementation(async () => Response.json({}));
		await call(GET, 'GET', 'launches/catalogue');
		await call(GET, 'GET', 'launches');
		await call(POST, 'POST', 'launches', { model: 'a/b' });
		await call(GET, 'GET', 'launches/12');
		await call(DELETE, 'DELETE', 'launches/12');
		expect(vi.mocked(flockAnswer).mock.calls.map(([url, init]) => [init?.method, url])).toEqual([
			['GET', '/api/v1/orgs/acme/evals/launches/catalogue'],
			['GET', '/api/v1/orgs/acme/evals/launches'],
			['POST', '/api/v1/orgs/acme/evals/launches'],
			['GET', '/api/v1/orgs/acme/evals/launches/12'],
			['DELETE', '/api/v1/orgs/acme/evals/launches/12']
		]);
	});

	it('refuses any other route or method without asking Flock', async () => {
		const refused = [
			call(GET, 'GET', 'runs'),
			call(GET, 'GET', 'launches/catalogue/x'),
			call(GET, 'GET', 'launches/abc'),
			call(POST, 'POST', 'launches/12'),
			call(POST, 'POST', 'launches/catalogue'),
			call(DELETE, 'DELETE', 'launches'),
			call(DELETE, 'DELETE', 'launches/catalogue')
		];
		for (const answer of refused) await expect(answer).rejects.toMatchObject({ status: 404 });
		expect(flockAnswer).not.toHaveBeenCalled();
	});

	it('hands back a refusal with Flock’s error as its message', async () => {
		vi.mocked(flockAnswer).mockResolvedValue(
			Response.json({ error: 'this month’s launches reached the cap' }, { status: 409 })
		);
		const res = await call(POST, 'POST', 'launches', { model: 'a/b' });
		expect(res.status).toBe(409);
		expect(await res.json()).toEqual({ message: 'this month’s launches reached the cap' });
	});
});
