import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flockAnswer } from './flock.ts';
import { evalRuns } from './models.ts';

vi.mock('./flock.ts', () => ({ flockAnswer: vi.fn() }));

beforeEach(() => vi.resetAllMocks());

describe('a version’s eval runs', () => {
	it('reads each run’s numeric summary, leaving out runs that can’t be read', async () => {
		vi.mocked(flockAnswer).mockImplementation(async (url) => {
			if (url.endsWith('/versions/12'))
				return Response.json({ evals: ['acme/evals/e1', 'acme/evals/gone'] });
			if (url.endsWith('/runs/gone')) return Response.json({ error: 'no' }, { status: 404 });
			return Response.json({
				display_name: 'mmlu',
				state: 'finished',
				created_at: 1,
				summary: { 'mmlu/acc': 0.5, note: 'text', nan: null }
			});
		});
		const res = await evalRuns('acme', 'olmo2-1b', 12);
		expect(await res.json()).toEqual({
			runs: [
				{
					path: 'acme/evals/e1',
					entity: 'acme',
					project: 'evals',
					id: 'e1',
					display_name: 'mmlu',
					state: 'finished',
					created_at: 1,
					metrics: { 'mmlu/acc': 0.5 }
				}
			]
		});
		expect(vi.mocked(flockAnswer).mock.calls.map(([url]) => url)).toEqual([
			'/api/v1/orgs/acme/models/olmo2-1b/versions/12',
			'/api/v1/projects/acme/evals/runs/e1',
			'/api/v1/projects/acme/evals/runs/gone'
		]);
	});

	it('passes a refusal on as the dialogs read one', async () => {
		vi.mocked(flockAnswer).mockResolvedValue(
			Response.json({ error: 'version 9 of acme/m not found' }, { status: 404 })
		);
		const res = await evalRuns('acme', 'm', 9);
		expect(res.status).toBe(404);
		expect(await res.json()).toEqual({ message: 'version 9 of acme/m not found' });
	});
});
