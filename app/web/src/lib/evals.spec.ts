import { describe, expect, it } from 'vitest';
import {
	compareHref,
	evalRows,
	evalScores,
	isEvalResults,
	itemsViewOf,
	withItemsView
} from './evals.ts';
import type { EvalResults, ModelEvalRun } from './types.ts';

type Task = [name: string, metric: string, value: number | null, hash?: string];

const results = (suites: [string, number | null][], tasks: Task[]) =>
	({
		schema_version: 9,
		provenance: {
			model: 'tiny@0',
			model_digest: null,
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
		suites: suites.map(([suite, score]) => ({
			suite,
			suite_hash: `s-${suite}`,
			members: [],
			score,
			metric: null
		})),
		tasks: tasks.map(([task, primary_metric, value, hash]) => ({
			task,
			task_hash: hash ?? `h-${task}`,
			primary_metric,
			metrics: { [primary_metric]: value, bpb: 9 },
			num_docs: 2,
			dataset: {
				repo: 'allenai/ai2_arc',
				revision: 'r',
				subset: null,
				split: 'test',
				private: false
			},
			truncation: { requests: 0, docs: 0, max_tokens_cut: 0 }
		}))
	}) as unknown as EvalResults;

describe('eval scores', () => {
	it('gives suites, then each task’s primary metric, one row per name and hash', () => {
		const scores = evalScores(
			results(
				[
					['core', 0.4],
					['group', null]
				],
				[['arc', 'acc_raw', 0.5]]
			)
		);
		expect(scores.rows.map((r) => r.key)).toEqual(['suites/core@s-core', 'arc/acc_raw@h-arc']);
		expect(scores.values).toEqual({ 'suites/core@s-core': 0.4, 'arc/acc_raw@h-arc': 0.5 });
	});

	it('keeps a non-finite score as null rather than dropping its row', () => {
		const scores = evalScores(results([], [['mmlu', 'acc_raw', null]]));
		expect(scores.values).toEqual({ 'mmlu/acc_raw@h-mmlu': null });
	});

	it('lists every run’s rows once, suites first, and a task scored two ways twice, together', () => {
		const run = (r: EvalResults | null) => ({ scores: r && evalScores(r) }) as ModelEvalRun;
		const rows = evalRows([
			run(results([], [['b', 'acc_raw', 1]])),
			run(null),
			run(
				results(
					[['core', 1]],
					[
						['a', 'acc_raw', 1],
						['b', 'acc_raw', 0, 'other']
					]
				)
			)
		]);
		expect(rows.map((r) => r.key)).toEqual([
			'suites/core@s-core',
			'b/acc_raw@h-b',
			'b/acc_raw@other',
			'a/acc_raw@h-a'
		]);
	});
});

describe('reading a results file', () => {
	it('takes a whole file of schema 8 to 11, and any other schema for its note', () => {
		const file = results([['core', 1]], [['arc', 'acc_raw', 1]]);
		expect(isEvalResults(file)).toBe(true);
		const scoped = {
			...file,
			schema_version: 10,
			provenance: {
				...file.provenance,
				model: 'flock:pretrain:sweeps/lr-3e-4@12',
				model_org: 'acme',
				model_project: 'pretrain',
				model_path: 'sweeps/lr-3e-4'
			}
		};
		expect(isEvalResults(scoped)).toBe(true);
		expect(isEvalResults({ schema_version: 10 })).toBe(false);
		expect(isEvalResults({ schema_version: 12 })).toBe(true);
	});

	it('refuses what the pages would crash on', () => {
		const good = results([['core', 1]], [['arc', 'acc_raw', 1]]);
		const broken: unknown[] = [
			null,
			[],
			{ schema_version: '9' },
			{ ...good, suites: undefined },
			{ ...good, tasks: [{ ...good.tasks[0], task_hash: 1 }] },
			{ ...good, tasks: [{ ...good.tasks[0], dataset: null }] },
			// Schema 9 without the private mark, which eval's reader refuses too.
			{
				...good,
				tasks: [{ ...good.tasks[0], dataset: { ...good.tasks[0].dataset, private: undefined } }]
			},
			{ ...good, tasks: [{ ...good.tasks[0], metrics: { acc_raw: '0.5' } }] },
			{ ...good, tasks: [good.tasks[0], good.tasks[0]] }, // a repeated name
			{ ...good, suites: [{ ...good.suites[0], score: 'high' }] },
			{ ...good, suites: [{ ...good.suites[0], members: [{ toString: 1 }] }] },
			{ ...good, provenance: {} },
			{ ...good, tasks: [{ ...good.tasks[0], truncation: {} }] },
			{ ...good, provenance: { ...good.provenance, torch: undefined } }
		];
		for (const value of broken) expect(isEvalResults(value), JSON.stringify(value)).toBe(false);
	});
});

describe('compare links', () => {
	it('names the other run in the query', () => {
		expect(compareHref('acme', 'evals', 'e1', 'acme/other evals/e2')).toBe(
			'/acme/evals/runs/e1/compare?with=acme%2Fother%20evals%2Fe2'
		);
	});
});

describe('items links', () => {
	it('carry the list’s state beside the page’s own params, defaults left out', () => {
		const url = { pathname: '/acme/evals/runs/e1/compare', search: '?with=acme%2Fevals%2Fe2' };
		const view = { task: 'arc_easy', keep: 'only_b' as const, page: 3, item: 'set/7' };
		const href = withItemsView(url, view);
		expect(href).toBe(
			'/acme/evals/runs/e1/compare?with=acme%2Fevals%2Fe2&task=arc_easy&keep=only_b&page=3&item=set%2F7'
		);
		expect(itemsViewOf(new URL(href, 'https://flock.test').searchParams)).toEqual({
			...view,
			sort: undefined
		});
		// Back to the defaults: the list's params go, the page's stay.
		const search = href.slice(href.indexOf('?'));
		expect(withItemsView({ ...url, search }, {})).toBe(`${url.pathname}${url.search}`);
	});

	it('ignore a page that isn’t a whole number past the first', () => {
		for (const page of ['0', '1', '2.5', 'x', '-3'])
			expect(itemsViewOf(new URLSearchParams({ page })).page, page).toBeUndefined();
		expect(itemsViewOf(new URLSearchParams({ sort: 'margin' })).sort).toBeUndefined();
	});
});
