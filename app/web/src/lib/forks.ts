/** Forks and blends (023): the rules and wording the dialogs share with the API. */

import { nameOf, parentOf } from './datasets.ts';
import type { BlendColumn, BlendPreview, ColumnFrom, DatasetJob } from './types.ts';

/** What the blend dialog starts with: the selected datasets, or a blend to make again. */
export interface BlendStart {
	sources: { path: string; version?: string; amount?: string }[];
	mode?: 'rows' | 'fraction';
	total?: string;
	seed?: number;
	replace?: boolean;
	columns?: BlendColumn[] | null;
	to?: string;
}

/** The seed a new blend starts with (D174), as the trainer's sampler does. */
export const DEFAULT_SEED = 42;
/** The largest seed the API takes. */
export const MAX_SEED = 2n ** 63n - 1n;

/** A random seed, for the dialog's button. */
export const randomSeed = () => Math.floor(Math.random() * 2 ** 31);

/** Where a fork of `path` goes by default: beside it, `{name}-fork`, then `-fork-2`… */
export function forkPath(path: string, taken: ReadonlySet<string> = new Set()): string {
	const folder = parentOf(path);
	const base = `${nameOf(path)}-fork`;
	for (let n = 1; ; n++) {
		const name = n === 1 ? base : `${base}-${n}`;
		const candidate = folder ? `${folder}/${name}` : name;
		if (!taken.has(candidate)) return candidate;
	}
}

/** Casts that keep every value (D175), from a source's type to the output's. */
const INTS = ['int8', 'int16', 'int32', 'int64'];
const UINTS = ['uint8', 'uint16', 'uint32', 'uint64'];
const SAFE: Record<string, string[]> = {
	halffloat: ['float', 'double'],
	float: ['double'],
	string: ['large_string'],
	binary: ['large_binary']
};
INTS.forEach((t, i) => {
	SAFE[t] = [...INTS.slice(i + 1), ...(i < 3 ? ['double'] : []), ...(i < 2 ? ['float'] : [])];
});
UINTS.forEach((t, i) => {
	SAFE[t] = [
		...UINTS.slice(i + 1),
		...INTS.slice(i + 1),
		...(i < 3 ? ['double'] : []),
		...(i < 2 ? ['float'] : [])
	];
});

/** Whether a column of type `from` can become `to` without losing values. */
export const safeCast = (from: string, to: string) => (SAFE[from] ?? []).includes(to);

/** The column types the output menu offers: the sources' own, then the common ones. */
export function typeChoices(preview: BlendPreview | null, current: string): string[] {
	const seen = new Set<string>();
	for (const s of preview?.sources ?? []) for (const f of s.schema) seen.add(f.type);
	for (const t of ['string', 'large_string', 'int64', 'int32', 'double', 'float', 'bool'])
		seen.add(t);
	seen.add(current);
	return [...seen];
}

/** How a cell differs from taking the column of the same name and type, for its mark. */
export function cellMark(
	column: BlendColumn,
	source: string
): 'renamed' | 'cast' | 'null' | 'value' | null {
	const from = column.from[source];
	if (!from) return null;
	if ('null' in from) return 'null';
	if ('value' in from) return 'value';
	if (from.cast) return 'cast';
	return from.column !== column.name ? 'renamed' : null;
}

/** Whether the map needs no attention: no problems, and nothing renamed, cast or filled. */
export function allMatch(columns: BlendColumn[], problems: string[]): boolean {
	return (
		!problems.length &&
		columns.every((c) => Object.keys(c.from).every((s) => cellMark(c, s) === null))
	);
}

/** The problems that concern one output column, by its name at the start of the message. */
export const columnProblems = (problems: string[], name: string) =>
	problems.filter((p) => p.startsWith(`${name}:`) || p.startsWith(`${name} `));

/** The output column a source column becomes when added from "Not in the blend". */
export function addedColumn(
	name: string,
	type: string,
	source: string,
	sources: string[],
	taken: ReadonlySet<string>
): BlendColumn {
	let out = name;
	for (let n = 2; taken.has(out); n++) out = `${name}_${n}`;
	const from: Record<string, ColumnFrom> = {};
	for (const s of sources) from[s] = s === source ? { column: name } : { null: true };
	return { name: out, type, nullable: true, from };
}

/** The `source` column, filled with each source's name (its path, or what was typed). */
export function sourceColumn(sources: string[], taken: ReadonlySet<string>): BlendColumn {
	let name = 'source';
	for (let n = 2; taken.has(name); n++) name = `source_${n}`;
	return {
		name,
		type: 'string',
		nullable: false,
		from: Object.fromEntries(sources.map((s) => [s, { value: s }]))
	};
}

/** A value typed for an output column, as JSON wants it for that type. */
export function parseValue(text: string, type: string): string | number | boolean {
	if (type === 'bool') return text.trim().toLowerCase() === 'true';
	if (/^u?int\d+$/.test(type) || ['float', 'double', 'halffloat'].includes(type)) {
		const n = Number(text);
		return text.trim() === '' || Number.isNaN(n) ? text : n;
	}
	return text;
}

/** One line of a running or ended job, for its row above the listing. */
export function jobText(job: DatasetJob): string {
	const what = job.kind === 'blend' ? 'Blend' : 'Making independent';
	if (job.state === 'running') {
		const pct = Math.round(job.progress * 100);
		return `${what} ${job.dest}: ${job.phase ?? 'starting'}, ${pct}%`;
	}
	if (job.state === 'finished')
		return job.kind === 'blend'
			? `Blended ${job.result?.rows.toLocaleString('en-US') ?? ''} rows into ${job.dest}.`
			: `${job.dest} is now independent.`;
	if (job.state === 'cancelled') return `${what} ${job.dest} was cancelled.`;
	if (job.state === 'interrupted') return `${what} ${job.dest} stopped when the server restarted.`;
	return `${what} ${job.dest} failed.`;
}

/** The jobs whose dataset is in `folder` (the root holds everything). */
export const jobsAt = (jobs: DatasetJob[], folder: string) =>
	jobs.filter((j) => parentOf(j.dest) === folder);
