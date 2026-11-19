import { afterEach, describe, expect, it, vi } from 'vitest';
import { duration, loadCompute, normalizeSnapshot, type SlurmResponse } from './compute.ts';

afterEach(() => vi.unstubAllEnvs());

function configure() {
	vi.stubEnv('FLOCK_SLURM_URL', 'https://queue.example.test');
	vi.stubEnv('FLOCK_SLURM_ORG', 'cerulean');
	vi.stubEnv('FLOCK_SLURM_ACCOUNTS', ' plan-a,plan-b ');
	vi.stubEnv('FLOCK_SLURM_ACCESS_CLIENT_ID', 'client-id');
	vi.stubEnv('FLOCK_SLURM_ACCESS_CLIENT_SECRET', 'client-secret');
}
const emptyResponse: SlurmResponse = {
	data: { jobs: [], partitions: [] },
	clusters: [{ id: 'nano4', name: 'Nano4', enabled: true }],
	summary: { state: 'ok' }
};

describe('Slurm connection', () => {
	it('does not fetch a dashboard assigned to another organization', async () => {
		configure();
		const fetcher = vi.fn();
		const result = await loadCompute('other', fetcher);
		expect(fetcher).not.toHaveBeenCalled();
		expect(result.jobs).toEqual([]);
		expect(result.message).toContain('not configured');
	});
	it('sends Access credentials only in the server request headers', async () => {
		configure();
		const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(emptyResponse));
		const result = await loadCompute('cerulean', fetcher);
		const [url, init] = fetcher.mock.calls[0];
		expect(String(url)).toBe('https://queue.example.test/api/jobs?cluster=all');
		expect(new Headers(init?.headers).get('CF-Access-Client-Secret')).toBe('client-secret');
		expect(init?.redirect).toBe('error');
		expect(result.planAccounts).toEqual(['plan-a', 'plan-b']);
		expect(JSON.stringify(result)).not.toContain('client-secret');
	});
	it('returns a visible failure instead of presenting an inaccessible service as an empty healthy queue', async () => {
		configure();
		const result = await loadCompute(
			'cerulean',
			vi.fn().mockResolvedValue(new Response('denied', { status: 403 }))
		);
		expect(result.message).toContain('Unable to load');
		expect(result.jobs).toEqual([]);
	});
	it('preserves unavailable node counts and unlimited partition limits', () => {
		const result = normalizeSnapshot(
			{
				...emptyResponse,
				data: {
					jobs: [],
					partitions: [
						{
							key: 'nano4:gpu',
							clusterId: 'nano4',
							name: 'gpu',
							availability: 'up',
							timeLimitSeconds: null,
							timeLimitUnlimited: true,
							nodes: { allocated: null, idle: 0, other: 2 }
						}
					]
				}
			},
			[]
		);
		expect(result.partitions[0].allocated).toBeNull();
		expect(result.partitions[0].idle).toBe(0);
		expect(result.partitions[0].limitLabel).toBe('Unlimited');
		expect(duration(90061)).toBe('1d 1h 1m 1s');
	});
});
