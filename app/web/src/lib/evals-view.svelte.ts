import { SvelteSet } from 'svelte/reactivity';
import { evalTable, type EvalTableRow, type EvalTableRun } from '#lib/eval-table.ts';
import type { WorkspaceData } from '#lib/types.ts';
import type { Workspace } from '#lib/workspace/workspace.svelte.ts';

/** A suite's column in the project's table: the key the Columns menu hides and moves. */
export const suiteKey = (suite: string) => `summary.suites/${suite}`;

/**
 * A page's runs as the Evals preset reads them: only the columns it needs, which are the suite
 * scores, the tasks' metrics a suite's scale is read from, and the config `flock eval log` writes.
 */
export function evalRuns({ meta, page }: WorkspaceData): EvalTableRun[] {
	const reads =
		/^(summary\.suites\/|summary\..*\/(bpb|bits_per_byte|loss_per_token|perplexity|acc[^/]*)$|config\.suite_(members|hashes)\.|config\.(dtype|attention|sdpa_backend|batch_tokens|add_bos|device|model)$)/;
	const read = meta.columns.flatMap((c, i) => (reads.test(c.key) ? [[c.key, i] as const] : []));
	return page.runs.map((run, r) => {
		const values: Record<string, unknown> = {};
		for (const [key, i] of read) values[key] = page.num[i]?.[r] ?? page.text[i]?.[r];
		return { row: r, ordinal: run.ordinal, values };
	});
}

/**
 * The Evals preset over a workspace's page: its table, the suite columns it shows and in what
 * order, its sort, and whether runs on other settings are listed. The project page holds it, so
 * the toolbar and the table share it. Hidden and moved columns are the workspace's own column
 * settings, the same ones the runs table uses.
 */
export class EvalsView {
	/** Suites opened to show their member suites beside them. */
	readonly open = new SvelteSet<string>();
	/** Runs scored under other settings than most: hidden unless asked for. */
	showOther = $state(false);
	/**
	 * Sorted here, not by Flock: a mean rank isn't a stored column. Opens on the mean rank, best
	 * first, so the best model is the top row. A suite's key is `suite/<name>`.
	 */
	sort = $state<{ key: string; best: boolean } | null>({ key: 'mean', best: true });

	readonly #ws: Workspace;

	constructor(ws: Workspace) {
		this.#ws = ws;
	}

	/** Suites whose columns the viewer hid. */
	readonly hidden = $derived.by((): ReadonlySet<string> => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- replaced, never mutated
		const out = new Set<string>();
		for (const key of this.#ws.hiddenColumns)
			if (key.startsWith('summary.suites/')) out.add(key.slice('summary.suites/'.length));
		return out;
	});

	readonly table = $derived.by(() => {
		const ws = this.#ws;
		void ws.tableVersion;
		return evalTable(evalRuns(ws), this.hidden);
	});

	/**
	 * Every scored suite in the table's order: top suites by group, accuracy first, each followed
	 * by the suites it holds, until the viewer moves one in the Columns menu; then the viewer's.
	 */
	readonly ordered = $derived.by(() => {
		const out: string[] = [];
		const add = (suite: string) => {
			if (out.includes(suite)) return;
			out.push(suite);
			for (const child of this.table.columns[suite].children) add(child);
		};
		// A group's top suites stay together (`base_easy`'s accuracy and bits-per-byte columns),
		// groups in the order their first suite comes, each top suite with the suites it holds.
		const groups: string[][] = [];
		for (const suite of this.table.top) {
			const group = groups.find((g) => g[0].split(':')[0] === suite.split(':')[0]);
			if (group) group.push(suite);
			else groups.push([suite]);
		}
		for (const suite of groups.flat()) add(suite);
		const ws = this.#ws;
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a lookup, never mutated
		const at = new Map(ws.arrangedColumns.map((c, i) => [ws.meta.columns[c].key, i]));
		const arranged = out.toSorted((a, b) => at.get(suiteKey(a))! - at.get(suiteKey(b))!);
		// Unmoved suites keep the table's name order there (a move of another column saves every
		// key without moving them): only an order the viewer gave the suites replaces this one.
		const byName = out.toSorted((a, b) => (suiteKey(a) < suiteKey(b) ? -1 : 1));
		return arranged.every((suite, i) => suite === byName[i]) ? out : arranged;
	});

	/** The suites' columns, in order, for the Columns menu. */
	readonly keys = $derived(this.ordered.map(suiteKey));

	/**
	 * The columns shown, in the suites' order: the top suites and the members of opened ones,
	 * hidden ones left out, each with how deep it sits below a top suite.
	 */
	readonly shown = $derived.by(() => {
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- built here, never kept
		const depth = new Map<string, number>();
		const add = (suite: string, d: number) => {
			if (this.hidden.has(suite) || depth.has(suite)) return;
			depth.set(suite, d);
			if (this.open.has(suite)) for (const c of this.table.columns[suite].children) add(c, d + 1);
		};
		for (const suite of this.table.top) add(suite, 0);
		return this.ordered.flatMap((suite) =>
			depth.has(suite) ? [{ suite, depth: depth.get(suite)! }] : []
		);
	});

	/** Open every suite above `suite`, so its column shows. */
	reveal(suite: string) {
		for (const [parent, column] of Object.entries(this.table.columns))
			if (column.children.includes(suite) && parent !== suite && !this.open.has(parent)) {
				this.open.add(parent);
				this.reveal(parent);
			}
	}

	/** Runs scored under other settings than most. */
	readonly others = $derived(this.table.rows.filter((r) => r.differs.length).length);

	/** The rows listed, in the sort's order; a run without a value goes last either way. */
	readonly rows = $derived.by(() => {
		const out = this.table.rows.filter((r) => this.showOther || !r.differs.length);
		const sort = this.sort;
		const suite = sort?.key.startsWith('suite/') ? sort.key.slice('suite/'.length) : null;
		const column = suite === null ? undefined : this.table.columns[suite];
		// A suite sorted by on an earlier page that this page has no score for: page order.
		if (!sort || (suite !== null && !column)) return out;
		if (sort.key === 'name') {
			const name = (row: EvalTableRow) => this.#ws.nameOf(row.row);
			return out.toSorted((a, b) => (sort.best ? 1 : -1) * name(a).localeCompare(name(b)));
		}
		// Only ranked scores order a column: the others follow, as runs without a value do.
		const valueOf = (row: EvalTableRow) =>
			suite === null
				? row.meanRank
				: row.cells[suite].rank === null
					? null
					: row.cells[suite].value;
		// Lower is better for ranks, and on every scale but accuracy.
		const sign = (!column || column.scale !== 'accuracy') === sort.best ? 1 : -1;
		return out.toSorted((a, b) => {
			const [x, y] = [valueOf(a), valueOf(b)];
			if (x === null || y === null) return Number(x === null) - Number(y === null);
			return sign * (x - y);
		});
	});

	/** Sort by a column, best first; again, the other way. */
	sortBy(key: string) {
		this.sort = this.sort?.key === key ? { key, best: !this.sort.best } : { key, best: true };
	}
}
