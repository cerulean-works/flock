import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flockAnswer } from './flock.ts';
import { evalRuns } from './models.ts';

vi.mock('./flock.ts', () => ({
	flock: vi.fn(),
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
	dataset: { repo: 'allenai/ai2_arc', revision: 'r', subset: null, split: 'test', private: false },
	truncation: { requests: 0, docs: 0, max_tokens_cut: 0 }
});

const RESULTS = {
	schema_version: 9,
	provenance: {
		model: 'flock:acme/olmo2-1b@12',
		model_digest: 'd12',
		flock_commit: 'abc',
		device: 'cpu',
		dtype: 'float32',
		attention: 'eager',
		created_at: '2026-11-21T00:00:00+00:00',
		torch: '2',
		transformers: '5',
		tokenizers: '0',
		datasets: '5',
		python: '3.14',
		batch_tokens: 64,
		max_length: 128
	},
	suites: [
		{ suite: 'core', suite_hash: 's', members: ['arc', 'mmlu'], metric: null, score: 0.4 },
		{ suite: 'group', suite_hash: 'g', members: ['core'], metric: null, score: null }
	],
	tasks: [task('arc', 'acc_per_char', 0.5), task('mmlu', 'acc_raw', null)]
};

describe('a version’s eval runs', () => {
	it('reads each run’s scores from its results file, leaving out runs that can’t be read', async () => {
		vi.mocked(flockAnswer).mockImplementation(async (url) => {
			if (url.endsWith('/-/versions/12'))
				return Response.json({
					model: 'acme/olmo2-1b',
					number: 12,
					digest: 'd12',
					evals: ['acme/evals/e1', 'acme/evals/old', 'acme/evals/gone', 'acme/evals/v11']
				});
			if (url.includes('/runs/gone')) return Response.json({ error: 'no' }, { status: 404 });
			if (url.endsWith('/e1/files/results.json')) return Response.json(RESULTS);
			// A run whose config later named this version, but whose file scored version 11.
			if (url.endsWith('/v11/files/results.json'))
				return Response.json({
					...RESULTS,
					provenance: {
						...RESULTS.provenance,
						model: 'flock:acme/olmo2-1b@11',
						model_digest: 'd11'
					}
				});
			if (url.endsWith('/files/results.json'))
				return Response.json({ error: 'file not found' }, { status: 404 });
			return Response.json({ display_name: 'base easy', state: 'finished', created_at: 1 });
		});
		const res = await evalRuns({ org: 'acme', project: null }, 'olmo2-1b', 12);
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
				},
				// Linked, but its results file names another version: no scores shown here.
				{
					...run,
					path: 'acme/evals/v11',
					id: 'v11',
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
			'/api/v1/orgs/acme/models/olmo2-1b/-/versions/12',
			'/api/v1/projects/acme/evals/runs/e1',
			'/api/v1/projects/acme/evals/runs/e1/files/results.json',
			'/api/v1/projects/acme/evals/runs/gone',
			'/api/v1/projects/acme/evals/runs/old',
			'/api/v1/projects/acme/evals/runs/old/files/results.json',
			'/api/v1/projects/acme/evals/runs/v11',
			'/api/v1/projects/acme/evals/runs/v11/files/results.json'
		]);
	});

	it('reads a project’s model in its scope, and needs the file to name the org', async () => {
		const scoped = {
			...RESULTS,
			schema_version: 10,
			provenance: {
				...RESULTS.provenance,
				model: 'flock:pretrain:sweeps/lr@3',
				model_digest: 'd3',
				model_org: 'acme',
				model_project: 'pretrain',
				model_path: 'sweeps/lr'
			}
		};
		vi.mocked(flockAnswer).mockImplementation(async (url) => {
			if (url.includes('/models/'))
				return Response.json({
					model: 'pretrain:sweeps/lr',
					name: 'sweeps/lr',
					number: 3,
					digest: 'd3',
					evals: ['acme/evals/ours', 'acme/evals/lab']
				});
			if (url.endsWith('/ours/files/results.json')) return Response.json(scoped);
			// Another org's project of the same name, with the same bytes.
			if (url.endsWith('/lab/files/results.json'))
				return Response.json({
					...scoped,
					provenance: { ...scoped.provenance, model_org: 'lab' }
				});
			return Response.json({ display_name: null, state: 'finished', created_at: 1 });
		});
		const res = await evalRuns({ org: 'acme', project: 'pretrain' }, 'sweeps/lr', 3);
		const runs = (await res.json()).runs as { id: string; scores: unknown }[];
		expect(runs.map((r) => [r.id, r.scores !== null])).toEqual([
			['ours', true],
			['lab', false]
		]);
		expect(vi.mocked(flockAnswer).mock.calls[0][0]).toBe(
			'/api/v1/orgs/acme/models/sweeps/lr/-/versions/3?project=pretrain'
		);
	});

	it('passes a refusal on as the dialogs read one', async () => {
		vi.mocked(flockAnswer).mockResolvedValue(
			Response.json({ error: 'version 9 of acme/m not found' }, { status: 404 })
		);
		const res = await evalRuns({ org: 'acme', project: null }, 'm', 9);
		expect(res.status).toBe(404);
		expect(await res.json()).toEqual({ message: 'version 9 of acme/m not found' });
	});
});
