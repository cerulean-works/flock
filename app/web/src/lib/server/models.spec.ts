import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flockAnswer } from './flock.ts';
import { evalRuns } from './models.ts';

vi.mock('./flock.ts', () => ({
	flockAnswer: vi.fn(),
	projectPath: (entity: string, project: string) => `/api/v1/projects/${entity}/${project}`
}));

beforeEach(() => vi.resetAllMocks());

const task = (name: string, metric: string, value: number | null) => ({
	task: name,
	task_hash: `h-${name}`,
	primary_metric: metric,
	metrics: { [metric]: value, bpb: 1 },
	num_docs: 2,
	dataset: { repo: 'allenai/ai2_arc' },
	truncation: { requests: 0, docs: 0, max_tokens_cut: 0 }
});

const RESULTS = {
	schema_version: 9,
	provenance: { model: 'flock:acme/olmo2-1b@12' },
	suites: [
		{ suite: 'core', suite_hash: 's', members: ['arc', 'mmlu'], metric: null, score: 0.4 },
		{ suite: 'group', suite_hash: 'g', members: ['core'], metric: null, score: null }
	],
	tasks: [task('arc', 'acc_per_char', 0.5), task('mmlu', 'acc_raw', null)]
};

describe('a version’s eval runs', () => {
	it('reads each run’s scores from its results file, leaving out runs that can’t be read', async () => {
		vi.mocked(flockAnswer).mockImplementation(async (url) => {
			if (url.endsWith('/versions/12'))
				return Response.json({ evals: ['acme/evals/e1', 'acme/evals/old', 'acme/evals/gone'] });
			if (url.includes('/runs/gone')) return Response.json({ error: 'no' }, { status: 404 });
			if (url.endsWith('/e1/files/results.json')) return Response.json(RESULTS);
			if (url.endsWith('/files/results.json'))
				return Response.json({ error: 'file not found' }, { status: 404 });
			return Response.json({ display_name: 'base easy', state: 'finished', created_at: 1 });
		});
		const res = await evalRuns('acme', 'olmo2-1b', 12);
		const run = { entity: 'acme', project: 'evals', display_name: 'base easy' };
		expect(await res.json()).toEqual({
			runs: [
				{
					...run,
					path: 'acme/evals/e1',
					id: 'e1',
					state: 'finished',
					created_at: 1,
					scores: {
						rows: [
							{ key: 'suites/core@s', kind: 'suite', name: 'core', metric: null, hash: 's' },
							{
								key: 'arc/acc_per_char@h-arc',
								kind: 'task',
								name: 'arc',
								metric: 'acc_per_char',
								hash: 'h-arc'
							},
							{
								key: 'mmlu/acc_raw@h-mmlu',
								kind: 'task',
								name: 'mmlu',
								metric: 'acc_raw',
								hash: 'h-mmlu'
							}
						],
						values: {
							'suites/core@s': 0.4,
							'arc/acc_per_char@h-arc': 0.5,
							'mmlu/acc_raw@h-mmlu': null
						}
					}
				},
				// No results file (an eval logged before 041): listed, with no scores.
				{
					...run,
					path: 'acme/evals/old',
					id: 'old',
					state: 'finished',
					created_at: 1,
					scores: null
				}
			]
		});
		// Eight runs are read at once, so the order of the calls isn't fixed.
		expect(
			vi
				.mocked(flockAnswer)
				.mock.calls.map(([url]) => url)
				.sort()
		).toEqual([
			'/api/v1/orgs/acme/models/olmo2-1b/versions/12',
			'/api/v1/projects/acme/evals/runs/e1',
			'/api/v1/projects/acme/evals/runs/e1/files/results.json',
			'/api/v1/projects/acme/evals/runs/gone',
			'/api/v1/projects/acme/evals/runs/old',
			'/api/v1/projects/acme/evals/runs/old/files/results.json'
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
