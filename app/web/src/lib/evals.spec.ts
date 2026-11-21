import { describe, expect, it } from 'vitest';
import { compareHref, evalRows, evalScores } from './evals.ts';
import type { EvalResults, ModelEvalRun } from './types.ts';

const results = (suites: [string, number | null][], tasks: [string, string, number | null][]) =>
	({
		schema_version: 9,
		suites: suites.map(([suite, score]) => ({ suite, score, metric: null })),
		tasks: tasks.map(([task, primary_metric, value]) => ({
			task,
			primary_metric,
			metrics: { [primary_metric]: value, bpb: 9 }
		}))
	}) as unknown as EvalResults;

describe('eval scores', () => {
	it('keys suites and each task’s primary metric as the run’s summary does', () => {
		const scores = evalScores(results([['core', 0.4], ['group', null]], [['arc', 'acc_raw', 0.5]]));
		expect(scores.rows.map((r) => r.key)).toEqual(['suites/core', 'arc/acc_raw']);
		expect(scores.values).toEqual({ 'suites/core': 0.4, 'arc/acc_raw': 0.5 });
	});

	it('keeps a non-finite score as null rather than dropping its row', () => {
		const scores = evalScores(results([], [['mmlu', 'acc_raw', null]]));
		expect(scores.values).toEqual({ 'mmlu/acc_raw': null });
	});

	it('lists every run’s rows once, suites first, in the first run’s order', () => {
		const run = (r: EvalResults | null) => ({ scores: r && evalScores(r) }) as ModelEvalRun;
		const rows = evalRows([
			run(results([], [['b', 'acc_raw', 1]])),
			run(null),
			run(results([['core', 1]], [['a', 'acc_raw', 1], ['b', 'acc_raw', 0]]))
		]);
		expect(rows.map((r) => r.key)).toEqual(['suites/core', 'b/acc_raw', 'a/acc_raw']);
	});
});

describe('compare links', () => {
	it('names the other run in the query', () => {
		expect(compareHref('acme', 'evals', 'e1', 'acme/other evals/e2')).toBe(
			'/acme/evals/runs/e1/compare?with=acme%2Fother%20evals%2Fe2'
		);
	});
});
