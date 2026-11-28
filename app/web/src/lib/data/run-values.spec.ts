import { afterEach, describe, expect, it, vi } from 'vitest';
import { forgetRunValues, runValues } from './run-values.svelte.ts';

afterEach(() => {
	forgetRunValues();
	vi.unstubAllGlobals();
});

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('runValues', () => {
	it('asks for many columns in turns of 32, so the ones sorted last are counted too', async () => {
		vi.stubGlobal('window', {});
		const asked: string[][] = [];
		vi.stubGlobal(
			'fetch',
			vi.fn(async (url: string) => {
				const keys = decodeURIComponent(new URL(url, 'http://x').searchParams.get('keys')!).split(
					','
				);
				asked.push(keys);
				const values = Object.fromEntries(keys.map((k) => [k, [{ value: `${k}-v`, runs: 1 }]]));
				return { ok: true, json: async () => ({ values }) };
			})
		);
		// A project with many config columns: owner sorts after 50 of them.
		const keys = [...Array.from({ length: 50 }, (_, i) => `config.k${i}`), 'owner', 'state'];
		expect(runValues('org', 'proj', keys)).toEqual({});
		await flush();
		expect(asked.map((k) => k.length)).toEqual([32, 20]);
		const values = runValues('org', 'proj', keys);
		expect(Object.keys(values)).toHaveLength(52);
		expect(values.owner).toEqual([{ value: 'owner-v', runs: 1 }]);
		// Read once: asking again doesn't fetch again.
		runValues('org', 'proj', keys);
		expect(asked).toHaveLength(2);
	});

	it('asks for a few columns in one request', async () => {
		vi.stubGlobal('window', {});
		const fetch = vi.fn<(url: string) => Promise<unknown>>(async () => ({
			ok: true,
			json: async () => ({ values: { owner: [] } })
		}));
		vi.stubGlobal('fetch', fetch);
		runValues('org', 'proj', ['state', 'owner']);
		await flush();
		expect(fetch).toHaveBeenCalledTimes(1);
		expect(fetch.mock.calls[0][0]).toContain(`keys=${encodeURIComponent('owner,state')}`);
	});
});
