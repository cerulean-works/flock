import { resolve } from '$app/paths';
import type {
	EvalResults,
	EvalRow,
	EvalScores,
	ItemKeep,
	ItemsView,
	ModelEvalRun
} from './types.ts';
import { withParams } from './workspace/url.ts';

/**
 * The schemas of eval's results file the dashboard reads (025 D184): eval's own reader's
 * `READ_SCHEMAS`, which `tests/app/test_evals.py` holds this list to.
 */
export const RESULT_SCHEMAS = [8, 9, 10, 11, 12];

/**
 * Whether a run file is a results file the dashboard can show (025 D184). It is the run's own
 * upload, so it's checked before any page trusts its shape: schemas 8 to 10 need every field
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
			!object(t.truncation) ||
			!(Number.isInteger(t.truncation.docs) && (t.truncation.docs as number) >= 0)
		)
			return false;
	for (const s of suites)
		if (
			!object(s) ||
			typeof s.suite !== 'string' ||
			typeof s.suite_hash !== 'string' ||
			!Array.isArray(s.members) ||
			!s.members.every((m) => typeof m === 'string') ||
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
 * An eval run's scores for the Evals tab (025 D183): each suite that has a score, then each
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
 * the order of the first run that has it. A name scored under two hashes keeps its rows
 * together, so the two are read one above the other.
 */
export function evalRows(runs: ModelEvalRun[]): EvalRow[] {
	const rows = new Map<string, EvalRow>();
	for (const run of runs)
		for (const row of run.scores?.rows ?? []) if (!rows.has(row.key)) rows.set(row.key, row);
	const byName = new Map<string, EvalRow[]>();
	for (const row of rows.values()) byName.set(row.name, [...(byName.get(row.name) ?? []), row]);
	const all = [...byName.values()].flat();
	return [...all.filter((r) => r.kind === 'suite'), ...all.filter((r) => r.kind === 'task')];
}

/**
 * Whether a metric is a share of docs right (`acc_raw`, `acc_per_char`, `accuracy`), shown as a
 * percent. A suite with no metric of its own averages its tasks' primary metrics, nearly always
 * accuracies: it reads as one when its score lies in [0, 1].
 */
export const isAccuracy = (metric: string | null, value: number) =>
	metric === null ? value >= 0 && value <= 1 : /^acc/.test(metric);

/**
 * A score as the eval pages show it: an accuracy as a percent to one decimal (`62.4%`), any other
 * metric (`bpb`, `perplexity`) to four significant figures. The exact value is the hover hint.
 */
export function formatScore(value: number | null | undefined, metric: string | null): string {
	if (value == null || !Number.isFinite(value)) return '–';
	if (isAccuracy(metric, value)) return `${(100 * value).toFixed(1)}%`;
	return String(+value.toPrecision(4));
}

/** Why eval's diff didn't compare a task, in words. */
export const NOT_COMPARED: Record<string, string> = {
	only_a: 'Scored by A only',
	only_b: 'Scored by B only',
	task_hash: 'Scored differently: another task hash',
	not_accuracy: 'Its primary metric isn’t an accuracy, so its docs have no right or wrong',
	no_predictions: 'A run has no predictions file you can read for it',
	no_outcome: 'A run’s predictions are older than schema 9, with no right or wrong per doc'
};

/**
 * The page comparing run A doc by doc with run B, given as `entity/project/id` (025 D185). With
 * no B, the page has A only and asks for B.
 */
export function compareHref(entity: string, project: string, run: string, other = '') {
	const page = resolve('/(app)/[entity]/[project]/runs/[run]/compare', { entity, project, run });
	return other ? `${page}?with=${encodeURIComponent(other)}` : page;
}

/** The items list's state as a link holds it (028), on the compare page and the Results tab. */
export function itemsViewOf(search: { get(key: string): string | null }): ItemsView {
	const page = Number(search.get('page'));
	return {
		// The list checks the task and keep against its own.
		task: search.get('task') ?? undefined,
		keep: (search.get('keep') as ItemKeep | null) ?? undefined,
		sort: search.get('sort') === 'doc' ? 'doc' : undefined,
		page: Number.isInteger(page) && page > 1 ? page : undefined,
		item: search.get('item') ?? undefined
	};
}

/** The current page with the items list's state in its search params, the rest kept. */
export const withItemsView = (url: { pathname: string; search: string }, view: ItemsView) =>
	withParams(url, {
		task: view.task,
		keep: view.keep,
		sort: view.sort,
		page: view.page?.toString(),
		item: view.item
	});

/** A margin as the items show it: its sign, then two decimals; a dash for none. */
export const signedMargin = (m: number | null) =>
	m === null ? '–' : `${m > 0 ? '+' : m < 0 ? '−' : ''}${Math.abs(m).toFixed(2)}`;

/**
 * Whether a key belongs to what it was typed in, not to a page's shortcuts: a field, a select or
 * list, a menu, a dialog or editable text.
 */
export const typedIn = (target: EventTarget | null) =>
	!!(target as Element | null)?.closest?.(
		'input, textarea, select, [role=combobox], [role=listbox], [role=menu], [role=dialog], [contenteditable=""], [contenteditable=true]'
	);
