/**
 * A project's eval runs by suites: the Evals preset of the runs table. Everything comes from
 * what `flock eval log` writes on a run: each scored suite's score as `summary.suites/<suite>`,
 * each task's metrics as `summary.<task>/<metric>`, and as config the scoring settings and each
 * suite's members and hash. Accuracy and bits per byte are never pooled; a run scored under
 * other settings, or a suite under another hash, is never ranked with the rest (one instrument).
 */

/** One run of the page, with the summary and config values the table reads, by column key. */
export interface EvalTableRun {
	/** Its row on the page. */
	row: number;
	/** Its position in its project: the lower, the older. */
	ordinal: number;
	values: Record<string, unknown>;
}

export interface EvalColumn {
	suite: string;
	/**
	 * Lower is better: the score is the mean of its members' bits per byte. Null when no run
	 * lists its members.
	 */
	lower: boolean | null;
	/** The suite hash most of the comparable runs scored it under; their ranks are by it. */
	hash: string | null;
	/** Its scored member suites, which open beside it. */
	children: string[];
}

export interface EvalCell {
	value: number | null;
	/** Scored under a suite hash other than the column's: not comparable, so shown as a dash. */
	otherHash: string | null;
	/** 1 is best; equal scores share a rank. Null where the cell isn't ranked. */
	rank: number | null;
	/** Where the score sits between the column's worst (0) and best (1). */
	heat: number | null;
}

export interface EvalTableRow {
	row: number;
	/** The settings it was scored under that differ from most runs'; empty when none do. */
	differs: string[];
	/** Its GPU when most runs were scored on another (an H100 beside H200 runs), else null. */
	device: string | null;
	/** Its model is instruction-tuned, by its name: scored with the same raw prompts. */
	instruct: boolean;
	cells: Record<string, EvalCell>;
	/** Its mean rank over the top accuracy suites, or null unless it has a rank in each. */
	accuracyRank: number | null;
	/** The same over the top bits-per-byte suites. */
	bpbRank: number | null;
}

export interface EvalTable {
	columns: Record<string, EvalColumn>;
	/** The suites no other scored suite holds: accuracy first, then bits per byte. */
	top: string[];
	/** Every run with a suite score, in page order. */
	rows: EvalTableRow[];
}

const numberAt = (run: EvalTableRun, key: string) => {
	const value = run.values[key];
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

/** The value most runs hold, as a string; a tie goes to the oldest run's. */
function mostCommon(runs: EvalTableRun[], valueOf: (run: EvalTableRun) => string | null) {
	const counts = new Map<string, { n: number; oldest: number }>();
	for (const run of runs) {
		const value = valueOf(run);
		if (value === null) continue;
		const seen = counts.get(value) ?? { n: 0, oldest: run.ordinal };
		counts.set(value, { n: seen.n + 1, oldest: Math.min(seen.oldest, run.ordinal) });
	}
	let best: string | null = null;
	for (const [value, { n, oldest }] of counts) {
		const held = best === null ? null : counts.get(best)!;
		if (!held || n > held.n || (n === held.n && oldest < held.oldest)) best = value;
	}
	return best;
}

/**
 * Whether `suite`'s score on `run` is the mean of its members' bits per byte: a member task's
 * `bpb` (`bits_per_byte` for perplexity tasks), a member suite's score when it is one too. A
 * scored suite's members share one scale (flock's `Suite`), so a suite of accuracies never
 * matches. Null when the run doesn't list its members.
 */
function isBitsPerByte(run: EvalTableRun, suite: string): boolean | null {
	const members = run.values[`config.suite_members.${suite}`];
	const score = numberAt(run, `summary.suites/${suite}`);
	if (!Array.isArray(members) || !members.length || score === null) return null;
	let sum = 0;
	for (const member of members) {
		const value = Array.isArray(run.values[`config.suite_members.${member}`])
			? isBitsPerByte(run, member)
				? numberAt(run, `summary.suites/${member}`)
				: null
			: (numberAt(run, `summary.${member}/bpb`) ??
				numberAt(run, `summary.${member}/bits_per_byte`));
		if (value === null) return false;
		sum += value;
	}
	// The mean as eval computes it, up to float rounding.
	return Math.abs(sum / members.length - score) <= 1e-9 * Math.max(1, Math.abs(score));
}

/** The Evals preset's table over a page's runs. */
export function evalTable(page: EvalTableRun[]): EvalTable {
	const runs = page.filter((run) =>
		Object.keys(run.values).some(
			(k) => k.startsWith('summary.suites/') && numberAt(run, k) !== null
		)
	);

	// One instrument: the scoring settings most runs share. A run scored under others is never
	// ranked with them.
	const settings = ['dtype', 'attention', 'sdpa_backend', 'batch_tokens', 'max_length', 'add_bos'];
	const usual = new Map<string, string | null>();
	for (const s of settings)
		usual.set(
			s,
			mostCommon(runs, (run) => JSON.stringify(run.values[`config.${s}`] ?? null))
		);
	const differs = new Map<EvalTableRun, string[]>();
	for (const run of runs)
		differs.set(
			run,
			settings.filter((s) => JSON.stringify(run.values[`config.${s}`] ?? null) !== usual.get(s))
		);
	const like = runs.filter((run) => !differs.get(run)!.length);

	const suites = new Set<string>();
	for (const run of runs)
		for (const key of Object.keys(run.values))
			if (key.startsWith('summary.suites/') && numberAt(run, key) !== null)
				suites.add(key.slice('summary.suites/'.length));

	const columns: Record<string, EvalColumn> = {};
	const held = new Set<string>();
	for (const suite of suites) {
		const scoring = like.filter((run) => numberAt(run, `summary.suites/${suite}`) !== null);
		const hash = mostCommon(scoring, (run) => {
			const value = run.values[`config.suite_hashes.${suite}`];
			return typeof value === 'string' ? value : null;
		});
		// Read on a run that scored it under the column's hash, the oldest first.
		let lower: boolean | null = null;
		for (const run of scoring.toSorted((a, b) => a.ordinal - b.ordinal)) {
			if (run.values[`config.suite_hashes.${suite}`] !== hash) continue;
			lower = isBitsPerByte(run, suite);
			if (lower !== null) break;
		}
		const lists = runs.map((run) => run.values[`config.suite_members.${suite}`]);
		const members = (lists.find(Array.isArray) ?? []) as string[];
		for (const list of lists) if (Array.isArray(list)) for (const m of list) held.add(m);
		columns[suite] = { suite, lower, hash, children: members.filter((m) => suites.has(m)) };
	}
	const top = [...suites]
		.filter((suite) => !held.has(suite))
		.sort(
			(a, b) =>
				Number(columns[a].lower === true) - Number(columns[b].lower === true) || (a < b ? -1 : 1)
		);

	const deviceOf = (run: EvalTableRun) => {
		const device = run.values['config.device'];
		return typeof device === 'string' ? device : null;
	};
	const usualDevice = mostCommon(runs, deviceOf);
	const rows: EvalTableRow[] = runs.map((run) => ({
		row: run.row,
		differs: differs.get(run)!,
		device: deviceOf(run) === usualDevice ? null : deviceOf(run),
		instruct: /instruct|chat/i.test(String(run.values['config.model'] ?? '')),
		cells: {},
		accuracyRank: null,
		bpbRank: null
	}));
	for (const suite of suites) {
		const { lower, hash } = columns[suite];
		const ranked: EvalCell[] = [];
		runs.forEach((run, i) => {
			const value = numberAt(run, `summary.suites/${suite}`);
			const scoredAs = run.values[`config.suite_hashes.${suite}`];
			// A run from before suite hashes has none: it isn't known to be comparable either.
			const otherHash = value !== null && scoredAs !== hash ? String(scoredAs ?? '') : null;
			const cell: EvalCell = { value, otherHash, rank: null, heat: null };
			rows[i].cells[suite] = cell;
			if (value !== null && otherHash === null && lower !== null && !rows[i].differs.length)
				ranked.push(cell);
		});
		const better = (a: number, b: number) => (lower ? a < b : a > b);
		const values = ranked.map((c) => c.value!);
		const best = lower ? Math.min(...values) : Math.max(...values);
		const worst = lower ? Math.max(...values) : Math.min(...values);
		for (const cell of ranked) {
			cell.rank = 1 + values.filter((v) => better(v, cell.value!)).length;
			cell.heat = best === worst ? null : (cell.value! - worst) / (best - worst);
		}
	}

	// Each mean over the top suites of one scale only: accuracy and bits per byte never pool.
	const accuracy = top.filter((suite) => columns[suite].lower === false);
	const bpb = top.filter((suite) => columns[suite].lower === true);
	for (const row of rows) {
		if (row.differs.length) continue;
		const mean = (over: string[]) => {
			let sum = 0;
			for (const suite of over) {
				const rank = row.cells[suite].rank;
				if (rank === null) return null;
				sum += rank;
			}
			return over.length ? sum / over.length : null;
		};
		row.accuracyRank = mean(accuracy);
		row.bpbRank = mean(bpb);
	}
	return { columns, top, rows };
}
