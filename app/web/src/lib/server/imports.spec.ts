import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flockAnswer } from './flock.ts';
import { relayImports } from './imports.ts';

vi.mock('./flock.ts', () => ({ flockAnswer: vi.fn() }));

beforeEach(() => vi.resetAllMocks());

describe('relaying Hugging Face imports', () => {
	it('passes the access token header on, and nothing else of the browser’s', async () => {
		vi.mocked(flockAnswer).mockResolvedValue(Response.json({ kind: 'dataset', configs: [] }));
		const request = new Request('http://flock.test/api/imports/acme/hf/preview?repo=a/b', {
			headers: { 'x-hf-token': 'hf_secret', cookie: 'session=1' }
		});
		const res = await relayImports('acme', 'preview', request);
		expect(res.status).toBe(200);
		const [url, init] = vi.mocked(flockAnswer).mock.calls[0];
		expect(url).toBe('/api/v1/orgs/acme/imports/hf/preview?repo=a/b');
		expect(init?.headers).toEqual({ 'x-hf-token': 'hf_secret' });
	});

	it('sends a start’s body, and hands back a refusal with its command', async () => {
		vi.mocked(flockAnswer).mockResolvedValue(
			Response.json(
				{ error: 'This model is 60 GB.', command: 'flock models import a/b acme/b' },
				{ status: 409 }
			)
		);
		const request = new Request('http://flock.test/api/imports/acme/hf', {
			method: 'POST',
			body: JSON.stringify({ kind: 'model', repo: 'a/b', name: 'b' })
		});
		const res = await relayImports('acme', '', request);
		expect(res.status).toBe(409);
		expect(await res.json()).toEqual({
			message: 'This model is 60 GB.',
			command: 'flock models import a/b acme/b'
		});
		const [url, init] = vi.mocked(flockAnswer).mock.calls[0];
		expect(url).toBe('/api/v1/orgs/acme/imports/hf');
		expect(init?.method).toBe('POST');
		expect(init?.headers).toEqual({ 'content-type': 'application/json' });
	});
});
