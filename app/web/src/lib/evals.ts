import { resolve } from '$app/paths';
import type { EvalResults, EvalRow, EvalScores, ModelEvalRun } from './types.ts';

/** The schemas of eval's results file the dashboard reads, as eval's own reader does (023 D173). */
export const RESULT_SCHEMAS = [8, 9];

/**
 * Whether a run file is a results file the dashboard can show (023 D173). It is the run's own
 * upload, so it's checked before any page trusts its shape: schemas 8 and 9 need every field
 * the pages read, with task and suite names each used once. Another schema only needs its
 * number, for the note that says it isn't read.
 */
export function isEvalResults(value: unknown): value is EvalResults {
	const object = (v: unknown): v is Record<string, unknown> =>
		typeof v === 'object' && v !== null && !Array.isArray(v);
	if (!object(value) || typeof value.schema_version !== 'number') return false;
	if (!RESULT_SCHEMAS.includes(value.schema_version)) return true;
	const { provenance, tasks, suites } = value;
	if (!object(provenance)) return false;
	// The fields the Results tab shows; `model_digest`, `add_bos` and `sdpa_backend` came later
	// and may be missing.
	const strings = ['model', 'flock_commit', 'device', 'dtype', 'attention', 'created_at'];
	const versions = ['torch', 'transformers', 'tokenizers', 'datasets', 'python'];
	if (![...strings, ...versions].every((key) => typeof provenance[key] === 'string')) return false;
	if (typeof provenance.batch_tokens !== 'number' || typeof provenance.max_length !== 'number')
		return false;
	if (!Array.isArray(tasks) || !Array.isArray(suites)) return false;
	for (const t of tasks)
		if (
			!object(t) ||
			typeof t.task !== 'string' ||
			typeof t.task_hash !== 'string' ||
			typeof t.primary_metric !== 'string' ||
			typeof t.num_docs !== 'number' ||
			!object(t.metrics) ||
			// Scores are numbers, or null where eval wrote a non-finite one.
			!Object.values(t.metrics).every((v) => typeof v === 'number' || v === null) ||
			!object(t.dataset) ||
			typeof t.dataset.repo !== 'string' ||
			typeof t.dataset.revision !== 'string' ||
			typeof t.dataset.split !== 'string' ||
			!(typeof t.dataset.subset === 'string' || t.dataset.subset === null) ||
			// Schema 9 always writes the private mark (eval's reader refuses a file without it).
			(value.schema_version >= 9 && typeof t.dataset.private !== 'boolean') ||
			!object(t.truncation)
		)
			return false;
	for (const s of suites)
		if (
			!object(s) ||
			typeof s.suite !== 'string' ||
			typeof s.suite_hash !== 'string' ||
			!Array.isArray(s.members) ||
			!(typeof s.score === 'number' || s.score === null)
		)
			return false;
	// Rows are keyed by name.
	const names = (list: Record<string, unknown>[], key: string) => new Set(list.map((e) => e[key]));
	return (
		names(tasks, 'task').size === tasks.length && names(suites, 'suite').size === suites.length
	);
}

/**
 * An eval run's scores for the Evals tab (023 D172): each suite that has a score, then each
 * task's primary metric. A row is one name scored under one hash, so a task scored two ways
 * gets two rows rather than two numbers that only look comparable.
 */
export function evalScores(results: EvalResults): EvalScores {
	const rows: EvalRow[] = [];
	const values: Record<string, number | null> = {};
	for (const suite of results.suites) {
		if (suite.score === null) continue; // a suite that only groups others, or no finite mean
		const key = `suites/${suite.suite}@${suite.suite_hash}`;
		rows.push({
			key,
			kind: 'suite',
			name: suite.suite,
			metric: suite.metric,
			hash: suite.suite_hash
		});
		values[key] = suite.score;
	}
	for (const task of results.tasks) {
		const key = `${task.task}/${task.primary_metric}@${task.task_hash}`;
		const row = { key, kind: 'task' as const, name: task.task, metric: task.primary_metric };
		rows.push({ ...row, hash: task.task_hash });
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
