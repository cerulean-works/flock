/**
 * A project's eval runs by suites: the Evals preset of the runs table. Everything comes from
 * what `flock eval log` writes on a run: each scored suite's score as `summary.suites/<suite>`,
 * each task's metrics as `summary.<task>/<metric>`, and as config the scoring settings and each
 * suite's members and hash. A run scored under other settings, or a suite under another hash,
 * is never ranked with the rest (one instrument).
 */

/** One run of the page, with the summary and config values the table reads, by column key. */
export interface EvalTableRun {
	/** Its row on the page. */
	row: number;
	/** Its position in its project: the lower, the older. */
	ordinal: number;
	values: Record<string, unknown>;
}

/**
 * How a suite's score reads: an accuracy (higher is better), or the mean of its members' bits
 * per byte, loss per token or perplexity (lower is better).
 */
export type Scale = 'accuracy' | 'bpb' | 'loss_per_token' | 'perplexity';

export interface EvalColumn {
	suite: string;
	/** Read from the scores of its members; null when the runs don't agree or don't tell. */
	scale: Scale | null;
	/** The suite hash most of the comparable runs scored it under; their ranks are by it. */
	hash: string | null;
	/** Its scored member suites under that hash, which open beside it. */
	children: string[];
}

export interface EvalCell {
	value: number | null;
	/**
	 * Scored under a suite hash other than the column's: not comparable, so shown as a dash. A
	 * cell with no hash shows its score but is never ranked.
	 */
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
	/** Scored with a BOS token, as its model family's rule asks (Gemma): marked, still ranked. */
	bos: boolean;
	/** Its GPU when most runs were scored on another (an H100 beside H200 runs), else null. */
	device: string | null;
	/** Its model is instruction-tuned, by its name: scored with the same raw prompts. */
	instruct: boolean;
	cells: Record<string, EvalCell>;
	/**
	 * Its mean rank over the top suites of known scale, a convenience order as in a
	 * leaderboard, or null unless it has a rank in each.
	 */
	meanRank: number | null;
}

export interface EvalTable {
	columns: Record<string, EvalColumn>;
	/** The suites no other scored suite holds: accuracy first, then the rest. */
	top: string[];
	/** The top suites the mean rank is over: those with a ranked cell. */
	meanOver: string[];
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

const stringAt = (run: EvalTableRun, key: string) => {
	const value = run.values[key];
	return typeof value === 'string' ? value : null;
};

/** The Evals preset's table over a page's runs. */
export function evalTable(page: EvalTableRun[]): EvalTable {
	const runs = page.filter((run) =>
		Object.keys(run.values).some(
			(k) => k.startsWith('summary.suites/') && numberAt(run, k) !== null
		)
	);

	// One instrument: the settings most runs share, taken whole, so the winner is a set some run
	// was scored with. A run scored under others is never ranked with them. A model's maximum
	// length is its own, and BOS follows its family's rule, so neither is a setting here.
	const instrument = ['dtype', 'attention', 'sdpa_backend', 'batch_tokens'];
	const settingsOf = (run: EvalTableRun) =>
		instrument.map((s) => JSON.stringify(run.values[`config.${s}`] ?? null));
	const usual = JSON.parse(
		mostCommon(runs, (run) => JSON.stringify(settingsOf(run))) ?? '[]'
	) as string[];
	const differs = new Map<EvalTableRun, string[]>();
	for (const run of runs) {
		const settings = settingsOf(run);
		differs.set(
			run,
			instrument.filter((_, i) => settings[i] !== usual[i])
		);
	}
	const like = runs.filter((run) => !differs.get(run)!.length);

	// Each run's accuracies by task (`acc_raw`, `acc_per_char`, ...), for reading a suite's scale.
	const accuracies = new Map<EvalTableRun, Map<string, Map<string, number>>>();
	for (const run of runs) {
		const byTask = new Map<string, Map<string, number>>();
		for (const key of Object.keys(run.values)) {
			const match = /^summary\.(.+)\/(acc[^/]*)$/.exec(key);
			const value = numberAt(run, key);
			if (!match || value === null) continue;
			const metrics = byTask.get(match[1]) ?? new Map<string, number>();
			byTask.set(match[1], metrics.set(match[2], value));
		}
		accuracies.set(run, byTask);
	}

	/**
	 * The scale of `suite`'s score on `run`, read from its members, whose scores share one scale
	 * (eval's `Suite`). A scale fits exactly when the score is the mean of the members' values of
	 * its metric (a member suite's score when its scale is the same), up to float rounding; for
	 * accuracy, one `acc_*` metric for every member task. Exactly one exact fit decides, and two
	 * (a task whose bits per byte equal one of its accuracies) leave the scale unknown. With
	 * none, the score reads as accuracy when it lies within what the means of the members'
	 * accuracies allow, since a task's primary accuracy isn't logged. A suite that holds itself
	 * has no scale. Kept per run, as nested suites are read again from each parent.
	 */
	const scales = new Map<EvalTableRun, Map<string, Scale | null>>();
	const scaleOf = (run: EvalTableRun, suite: string): Scale | null => {
		const known = scales.get(run) ?? new Map<string, Scale | null>();
		scales.set(run, known);
		// A suite met again while its own scale is being read lists itself: no scale.
		if (known.has(suite)) return known.get(suite)!;
		known.set(suite, null);
		const members = run.values[`config.suite_members.${suite}`];
		const score = numberAt(run, `summary.suites/${suite}`);
		if (!Array.isArray(members) || !members.length || score === null) return null;
		const isSuite = (m: string) => Array.isArray(run.values[`config.suite_members.${m}`]);
		// Equal up to float rounding, or inside the bounds, at the score's scale.
		const near = (low: number, high: number) => {
			const slack = 1e-9 * Math.max(1, Math.abs(score));
			return low - slack <= score && score <= high + slack;
		};
		const fits: Scale[] = [];
		for (const scale of ['bpb', 'loss_per_token', 'perplexity'] as const) {
			const values = members.map((m) =>
				isSuite(m)
					? scaleOf(run, m) === scale
						? numberAt(run, `summary.suites/${m}`)
						: null
					: scale === 'bpb'
						? (numberAt(run, `summary.${m}/bpb`) ?? numberAt(run, `summary.${m}/bits_per_byte`))
						: numberAt(run, `summary.${m}/${scale}`)
			);
			if (values.every((v) => v !== null)) {
				const mean = values.reduce((a, b) => a + b, 0) / values.length;
				if (near(mean, mean)) fits.push(scale);
			}
		}
		// Each member's accuracies by metric; a member accuracy suite's score stands for any metric
		// (`*`). A member with none leaves accuracy out.
		const byMember = members.map((m) => {
			if (!isSuite(m)) return accuracies.get(run)!.get(m) ?? new Map<string, number>();
			const value = scaleOf(run, m) === 'accuracy' ? numberAt(run, `summary.suites/${m}`) : null;
			return new Map(value === null ? [] : [['*', value]]);
		});
		if (byMember.every((metrics) => metrics.size)) {
			const names = new Set(byMember.flatMap((metrics) => [...metrics.keys()]));
			const exact = [...names].some((name) => {
				const values = byMember.map((metrics) => metrics.get(name) ?? metrics.get('*'));
				if (values.some((v) => v === undefined)) return false;
				const mean = values.reduce((a, b) => a! + b!, 0)! / values.length;
				return near(mean, mean);
			});
			// Bounds only where nothing fits exactly: they are wide, and bits per byte can lie in them.
			const low = byMember.reduce((sum, m) => sum + Math.min(...m.values()), 0) / members.length;
			const high = byMember.reduce((sum, m) => sum + Math.max(...m.values()), 0) / members.length;
			if (exact || (!fits.length && near(low, high))) fits.push('accuracy');
		}
		const scale = fits.length === 1 ? fits[0] : null;
		known.set(suite, scale);
		return scale;
	};

	const suites = new Set<string>();
	for (const run of runs)
		for (const key of Object.keys(run.values))
			if (key.startsWith('summary.suites/') && numberAt(run, key) !== null)
				suites.add(key.slice('summary.suites/'.length));

	const columns: Record<string, EvalColumn> = {};
	const held = new Set<string>();
	for (const suite of suites) {
		const scored = runs.filter((run) => numberAt(run, `summary.suites/${suite}`) !== null);
		const comparable = scored.filter((run) => like.includes(run));
		// The hash of the runs ranked; with none of them, of the runs shown, which aren't ranked.
		const hashOf = (run: EvalTableRun) => stringAt(run, `config.suite_hashes.${suite}`);
		const hash = mostCommon(comparable.length ? comparable : scored, hashOf);
		const under = (comparable.length ? comparable : scored).filter(
			(run) => hash !== null && hashOf(run) === hash
		);
		// Every run under the column's hash must read the same scale, or the scale is unknown:
		// the runs ranked, or with none of them the runs shown, which are never ranked.
		const read = new Set(under.map((run) => scaleOf(run, suite)));
		// Its members as scored under the column's hash; with no hash known, as any run lists them.
		const members = (under.length ? under : scored)
			.map((run) => run.values[`config.suite_members.${suite}`])
			.find(Array.isArray) as string[] | undefined;
		for (const m of members ?? []) held.add(m);
		columns[suite] = {
			suite,
			scale: read.size === 1 ? [...read][0] : null,
			hash,
			children: (members ?? []).filter((m) => suites.has(m))
		};
	}
	// Suites that hold each other with no top suite above them get one top column, the first by
	// name among those no other unreached suite leads to from outside their group, so every
	// scored suite has a column to open and the choice follows membership, not names.
	const below = (suite: string, out = new Set<string>()) => {
		for (const child of columns[suite].children) if (!out.has(child)) below(child, out.add(child));
		return out;
	};
	const roots = [...suites].filter((suite) => !held.has(suite));
	const reached = new Set(roots.flatMap((suite) => [suite, ...below(suite)]));
	for (const suite of [...suites].sort()) {
		if (reached.has(suite)) continue;
		const fedFromOutside = [...suites].some(
			(other) => !reached.has(other) && below(other).has(suite) && !below(suite).has(other)
		);
		if (fedFromOutside) continue;
		roots.push(suite);
		for (const s of [suite, ...below(suite)]) reached.add(s);
	}
	const top = roots.sort(
		(a, b) =>
			Number(columns[a].scale !== 'accuracy') - Number(columns[b].scale !== 'accuracy') ||
			(a < b ? -1 : 1)
	);

	const deviceOf = (run: EvalTableRun) => stringAt(run, 'config.device');
	const usualDevice = mostCommon(runs, deviceOf);
	const rows: EvalTableRow[] = runs.map((run) => ({
		row: run.row,
		differs: differs.get(run)!,
		bos: run.values['config.add_bos'] === true,
		device: deviceOf(run) === usualDevice ? null : deviceOf(run),
		instruct: /instruct|chat/i.test(String(run.values['config.model'] ?? '')),
		cells: {},
		meanRank: null
	}));
	for (const suite of suites) {
		const { scale, hash } = columns[suite];
		const ranked: EvalCell[] = [];
		runs.forEach((run, i) => {
			const value = numberAt(run, `summary.suites/${suite}`);
			const scoredAs = stringAt(run, `config.suite_hashes.${suite}`);
			const otherHash =
				value !== null && scoredAs !== null && hash !== null && scoredAs !== hash ? scoredAs : null;
			const cell: EvalCell = { value, otherHash, rank: null, heat: null };
			rows[i].cells[suite] = cell;
			// Ranked only under the column's hash, known, on the usual settings and a known scale.
			if (value !== null && hash !== null && scoredAs === hash && scale !== null)
				if (!rows[i].differs.length) ranked.push(cell);
		});
		const lower = scale !== 'accuracy';
		const better = (a: number, b: number) => (lower ? a < b : a > b);
		const values = ranked.map((c) => c.value!);
		const best = lower ? Math.min(...values) : Math.max(...values);
		const worst = lower ? Math.max(...values) : Math.min(...values);
		for (const cell of ranked) {
			cell.rank = 1 + values.filter((v) => better(v, cell.value!)).length;
			cell.heat = best === worst ? null : (cell.value! - worst) / (best - worst);
		}
	}

	// One order over every top suite ranked in, accuracies and bits per byte alike: a
	// convenience for reading the table, which no claim cites. Opened member suites don't count,
	// so a suite and its members aren't counted twice.
	const meanOver = top.filter((suite) => rows.some((row) => row.cells[suite].rank !== null));
	for (const row of rows) {
		if (row.differs.length || !meanOver.length) continue;
		let sum = 0;
		for (const suite of meanOver) sum += row.cells[suite].rank ?? NaN;
		row.meanRank = Number.isNaN(sum) ? null : sum / meanOver.length;
	}
	return { columns, top, meanOver, rows };
}
