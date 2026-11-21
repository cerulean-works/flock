import { resolve } from '$app/paths';
import type { EvalResults, EvalRow, EvalScores, ModelEvalRun } from './types.ts';

/** The schemas of eval's results file the dashboard reads, as eval's own reader does (023 D173). */
export const RESULT_SCHEMAS = [8, 9];

/**
 * An eval run's scores for the Evals tab (023 D172): each suite that has a score, then each
 * task's primary metric, keyed as the run's summary names them.
 */
export function evalScores(results: EvalResults): EvalScores {
	const rows: EvalRow[] = [];
	const values: Record<string, number | null> = {};
	for (const suite of results.suites) {
		if (suite.score === null) continue; // a suite that only groups others
		const key = `suites/${suite.suite}`;
		rows.push({ key, kind: 'suite', name: suite.suite, metric: suite.metric });
		values[key] = suite.score;
	}
	for (const task of results.tasks) {
		const key = `${task.task}/${task.primary_metric}`;
		rows.push({ key, kind: 'task', name: task.task, metric: task.primary_metric });
		values[key] = task.metrics[task.primary_metric] ?? null;
	}
	return { rows, values };
}

/**
 * The Evals tab's rows over several runs: every row any run has, suites before tasks, each in
 * the order of the first run that has it.
 */
export function evalRows(runs: ModelEvalRun[]): EvalRow[] {
	const rows = new Map<string, EvalRow>();
	for (const run of runs)
		for (const row of run.scores?.rows ?? []) if (!rows.has(row.key)) rows.set(row.key, row);
	const all = [...rows.values()];
	return [...all.filter((r) => r.kind === 'suite'), ...all.filter((r) => r.kind === 'task')];
}

/** Why eval's diff didn't compare a task, in words. */
export const NOT_COMPARED: Record<string, string> = {
	only_a: 'Scored by A only',
	only_b: 'Scored by B only',
	task_hash: 'Scored differently: another task hash',
	not_accuracy: 'Its primary metric isn’t an accuracy, so its docs have no right or wrong',
	no_predictions: 'A run has no predictions file for it',
	no_outcome: 'A run’s predictions are older than schema 9, with no right or wrong per doc'
};

/** The page comparing run A doc by doc with run B, given as `entity/project/id` (023 D174). */
export const compareHref = (entity: string, project: string, run: string, other: string) =>
	`${resolve('/(app)/[entity]/[project]/runs/[run]/compare', { entity, project, run })}?with=${encodeURIComponent(other)}`;
