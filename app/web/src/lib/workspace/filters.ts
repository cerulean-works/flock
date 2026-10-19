import type { ColumnKind } from '#lib/types.ts';

// Run filters: what the workspace's Filter popover edits, how they travel in its URL
// (`?filters=[["state","eq","running"],{"any":[["config.lr","lt",0.01],["config.opt","eq","adam"]]}]`)
// and the Mongo-style filter Flock selects runs with.

export type FilterOp =
	'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'exists' | 'missing';

/** A value as Flock compares it: typed when the filter is made, from its column's kind. */
export type FilterValue = string | number | boolean;

/** One column compared to a value. */
export interface RunCondition {
	key: string;
	op: FilterOp;
	/** Null for `exists` and `missing`, which take none. */
	value: FilterValue | null;
}

/** Whether a group matches runs meeting all of its filters or any of them. */
export type FilterMatch = 'all' | 'any';

/** Filters combined: `state is running and (config.lr < 0.001 or config.opt is adam)`. */
export interface RunFilterGroup {
	match: FilterMatch;
	filters: RunFilter[];
}

/**
 * A condition or a group of them. A run page's filters all hold for every run shown; a page
 * matching any of several filters has them in one `any` group.
 */
export type RunFilter = RunCondition | RunFilterGroup;

export const isGroup = (f: RunFilter): f is RunFilterGroup => 'match' in f;

/** Operators that compare against no value. */
export const UNARY: ReadonlySet<FilterOp> = new Set(['exists', 'missing']);

const LABELS: Record<FilterOp, string> = {
	eq: 'is',
	ne: 'is not',
	gt: '>',
	gte: '≥',
	lt: '<',
	lte: '≤',
	contains: 'contains',
	exists: 'is set',
	missing: 'is not set'
};

/**
 * The operators that make sense for a column. Lists (`tags`) are `json`: Flock's `is` matches a
 * list containing the value. Unknown columns (typed by hand) get every operator.
 */
export function opsFor(kind: ColumnKind | undefined): { op: FilterOp; label: string }[] {
	const ops: FilterOp[] =
		kind === 'num' || kind === 'time' || kind === 'duration'
			? ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'exists', 'missing']
			: kind === 'str'
				? ['eq', 'ne', 'contains', 'exists', 'missing']
				: kind === 'state' || kind === 'bool'
					? ['eq', 'ne']
					: kind === 'json'
						? ['eq', 'ne', 'exists', 'missing']
						: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'exists', 'missing'];
	return ops.map((op) => ({ op, label: kind === 'json' && op === 'eq' ? 'includes' : LABELS[op] }));
}

export const opLabel = (op: FilterOp) => LABELS[op];

/**
 * A typed value from what was typed, for a column of `kind`; an error message when it doesn't
 * fit. Times take a date (`2026-10-01`, read as UTC) or seconds since the epoch.
 */
export function parseValue(
	text: string,
	kind: ColumnKind | undefined
): { value: FilterValue } | { error: string } {
	const raw = text.trim();
	if (!raw) return { error: 'Enter a value' };
	const number = Number(raw);
	switch (kind) {
		case 'num':
		case 'duration':
			return Number.isFinite(number) ? { value: number } : { error: `${raw} isn’t a number` };
		case 'time': {
			if (Number.isFinite(number)) return { value: number };
			const ms = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(raw) ? `${raw}T00:00:00Z` : raw);
			return Number.isFinite(ms) ? { value: ms / 1000 } : { error: `${raw} isn’t a date` };
		}
		case 'bool':
			return raw === 'true' || raw === 'false'
				? { value: raw === 'true' }
				: { error: 'Use true or false' };
		case 'str':
		case 'state':
			return { value: raw };
		default:
			// Lists, mixed and unknown columns: numbers and booleans as such, the rest as text.
			if (raw === 'true' || raw === 'false') return { value: raw === 'true' };
			return { value: Number.isFinite(number) ? number : raw };
	}
}

/** How a filter's value reads back in its input. */
export function valueText(value: FilterValue | null, kind: ColumnKind | undefined): string {
	if (value == null) return '';
	if (kind === 'time' && typeof value === 'number')
		return new Date(value * 1000).toISOString().slice(0, 10);
	return String(value);
}

const OPS = new Set(Object.keys(LABELS));

/** Groups deeper than this in a URL are dropped: the popover edits two levels. */
const MAX_DEPTH = 8;

type Encoded = [string, FilterOp, FilterValue | null] | { all: unknown[] } | { any: unknown[] };

function isCondition(f: unknown): f is [string, FilterOp, FilterValue | null] {
	if (!Array.isArray(f) || f.length !== 3) return false;
	const [key, op, value] = f;
	return (
		typeof key === 'string' &&
		key !== '' &&
		OPS.has(op) &&
		(UNARY.has(op) ? value == null : ['string', 'number', 'boolean'].includes(typeof value))
	);
}

/** The valid filters among `items`; empty groups and malformed entries are dropped. */
function decode(items: unknown[], depth: number): RunFilter[] {
	const out: RunFilter[] = [];
	for (const item of items) {
		if (isCondition(item)) {
			const [key, op, value] = item;
			out.push({ key, op, value: value ?? null });
			continue;
		}
		if (depth >= MAX_DEPTH || !item || typeof item !== 'object' || Array.isArray(item)) continue;
		const keys = Object.keys(item);
		const match = keys[0];
		if (keys.length !== 1 || (match !== 'all' && match !== 'any')) continue;
		const inner = (item as Record<string, unknown>)[match];
		if (!Array.isArray(inner)) continue;
		const filters = decode(inner, depth + 1);
		if (filters.length) out.push({ match, filters });
	}
	return out;
}

/**
 * Filters from the URL's `filters` param; anything malformed is dropped. Conditions are
 * `[key, op, value]` triples and groups `{"any": [...]}` or `{"all": [...]}`, so flat lists of
 * triples from before groups read as they did.
 */
export function readFilters(param: string | null): RunFilter[] {
	if (!param) return [];
	let parsed: unknown;
	try {
		parsed = JSON.parse(param);
	} catch {
		return [];
	}
	return Array.isArray(parsed) ? decode(parsed, 0) : [];
}

function encode(f: RunFilter): Encoded {
	if (isGroup(f)) return { [f.match]: f.filters.map(encode) } as Encoded;
	return [f.key, f.op, UNARY.has(f.op) ? null : f.value];
}

/** The URL's `filters` param, or null without any. */
export function writeFilters(filters: readonly RunFilter[]): string | null {
	if (!filters.length) return null;
	return JSON.stringify(filters.map(encode));
}

/** Conditions in some filters, counting those inside groups. */
export function countConditions(filters: readonly RunFilter[]): number {
	return filters.reduce((n, f) => n + (isGroup(f) ? countConditions(f.filters) : 1), 0);
}

/**
 * Filters as text, e.g. `state is running and (config.lr < 0.001 or config.opt is adam)`.
 * Top-level conditions go on lines of their own when `lines` is set. `kindOf` types values
 * (dates read back as dates).
 */
export function describeFilters(
	filters: readonly RunFilter[],
	kindOf: (key: string) => ColumnKind | undefined = () => undefined,
	lines = false
): string {
	const one = (f: RunFilter, nested: boolean): string => {
		if (!isGroup(f))
			return `${f.key} ${opLabel(f.op)}${UNARY.has(f.op) ? '' : ` ${valueText(f.value, kindOf(f.key))}`}`;
		const text = f.filters.map((g) => one(g, true)).join(f.match === 'any' ? ' or ' : ' and ');
		return nested && f.filters.length > 1 ? `(${text})` : text;
	};
	// A lone group is the top level: its own conditions, joined by its own word.
	const [only] = filters;
	const top = filters.length === 1 && isGroup(only) ? only : { match: 'all' as const, filters };
	const sep = lines ? '\n' : ' ';
	return top.filters
		.map((f) => one(f, top.filters.length > 1))
		.join(`${sep}${top.match === 'any' ? 'or' : 'and'} `);
}

/** `text` as a Python regular expression matching it literally. */
const literal = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Matches strings containing `text`, in any case. */
const containing = (text: string) => ({ $regex: `(?i)${literal(text)}` });

function condition(f: RunCondition): object {
	switch (f.op) {
		case 'exists':
			return { $exists: true };
		case 'missing':
			return { $exists: false };
		case 'contains':
			return containing(String(f.value));
		default:
			return { [`$${f.op}`]: f.value };
	}
}

/**
 * Flock's filter for one condition or group, or null for an empty group: with `$or` it would
 * match no run and with `$and` every run, neither of which was meant. A group of one is that
 * filter alone.
 */
function flockPart(f: RunFilter): object | null {
	if (!isGroup(f)) return { [f.key]: condition(f) };
	const parts = f.filters.map(flockPart).filter((p) => p != null);
	if (parts.length <= 1) return parts[0] ?? null;
	return { [f.match === 'any' ? '$or' : '$and']: parts };
}

/**
 * Flock's filter for a search and some filters, or null for every run. The search matches a
 * run's display name or id, in any case.
 */
export function flockFilter(search: string, filters: readonly RunFilter[]): object | null {
	const all = filters.map(flockPart).filter((p) => p != null);
	const q = search.trim();
	if (q) all.unshift({ $or: [{ display_name: containing(q) }, { name: containing(q) }] });
	if (!all.length) return null;
	return all.length === 1 ? all[0] : { $and: all };
}
