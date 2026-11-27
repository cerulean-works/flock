import type { ColumnKind, Owner } from '#lib/types.ts';
import { duration, formatNumber } from '#lib/utils.ts';

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

/**
 * Who the values of `user` columns (user ids) are, by id: the org's members, or the project's
 * owners (without emails) for a viewer outside the org.
 */
export type People = ReadonlyMap<number, Owner>;

const NOBODY: People = new Map();

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
 * list containing the value. A run's `owner` is someone or no one. Unknown columns (typed by
 * hand) get every operator.
 */
export function opsFor(kind: ColumnKind | undefined): { op: FilterOp; label: string }[] {
	const ops: FilterOp[] =
		kind === 'num' || kind === 'time' || kind === 'duration'
			? ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'exists', 'missing']
			: kind === 'str'
				? ['eq', 'ne', 'contains', 'exists', 'missing']
				: kind === 'state' || kind === 'bool'
					? ['eq', 'ne']
					: kind === 'json' || kind === 'user'
						? ['eq', 'ne', 'exists', 'missing']
						: ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'exists', 'missing'];
	return ops.map((op) => ({ op, label: kind === 'json' && op === 'eq' ? 'includes' : LABELS[op] }));
}

export const opLabel = (op: FilterOp) => LABELS[op];

/**
 * A typed value from what was typed, for a column of `kind`; an error message when it doesn't
 * fit. Times take a date (`2026-10-01`, read as UTC) or seconds since the epoch. Users take one
 * of `people`'s emails, a name no one else has, or a user id (of someone who left, say).
 */
export function parseValue(
	text: string,
	kind: ColumnKind | undefined,
	people: People = NOBODY
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
		case 'user': {
			if (Number.isInteger(number)) return { value: number };
			const q = raw.toLowerCase();
			const members = [...people.values()];
			const byEmail = members.find((p) => p.email?.toLowerCase() === q);
			if (byEmail) return { value: byEmail.id };
			const named = members.filter((p) => p.name.toLowerCase() === q);
			if (named.length === 1) return { value: named[0].id };
			return {
				error: named.length
					? `${named.length} members are named ${raw}: use an email`
					: `No member is ${raw}`
			};
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

/**
 * How a filter's value reads back in its input: users as their email if they're in `people`
 * (their name, when it doesn't say their email).
 */
export function valueText(
	value: FilterValue | null,
	kind: ColumnKind | undefined,
	people: People = NOBODY
): string {
	if (value == null) return '';
	if (kind === 'time' && typeof value === 'number')
		return new Date(value * 1000).toISOString().slice(0, 10);
	if (kind === 'user' && typeof value === 'number')
		return people.get(value)?.email ?? people.get(value)?.name ?? String(value);
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
 * (dates read back as dates, and users of `people` as their email).
 */
export function describeFilters(
	filters: readonly RunFilter[],
	kindOf: (key: string) => ColumnKind | undefined = () => undefined,
	lines = false,
	people: People = NOBODY
): string {
	const one = (f: RunFilter, nested: boolean): string => {
		if (!isGroup(f))
			return `${f.key} ${opLabel(f.op)}${UNARY.has(f.op) ? '' : ` ${valueText(f.value, kindOf(f.key), people)}`}`;
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

/** A filter chip's three parts, Linear's way: `State` `is any of` `Finished, Failed`. */
export interface ChipParts {
	/** The column as its header names it; empty for a filter built in Advanced. */
	label: string;
	/** The comparison, shown muted; empty with `label`. */
	op: string;
	value: string;
}

/** What chips need to name columns and values as the table shows them. */
export interface ChipNames {
	label?: (key: string) => string;
	kind?: (key: string) => ColumnKind | undefined;
	people?: People;
	/** The viewer's user id, shown as Me. */
	me?: number;
}

const CHIP_OPS: Record<FilterOp, string> = {
	eq: 'is',
	ne: 'is not',
	gt: 'above',
	gte: 'at least',
	lt: 'below',
	lte: 'at most',
	contains: 'contains',
	exists: 'is set',
	missing: 'is not set'
};

/** A chip's comparison: dates are after and before, lists include. */
function chipOp(op: FilterOp, kind: ColumnKind | undefined): string {
	if (kind === 'time' && (op === 'gt' || op === 'gte')) return 'after';
	if (kind === 'time' && (op === 'lt' || op === 'lte')) return 'before';
	if (kind === 'json' && op === 'eq') return 'includes';
	if (kind === 'json' && op === 'ne') return 'does not include';
	return CHIP_OPS[op];
}

/** A date in local time, `Nov 20`, with the year when it isn't this one and the time when set. */
function chipDate(seconds: number): string {
	const d = new Date(seconds * 1000);
	const year = d.getFullYear() !== new Date().getFullYear();
	const date = d.toLocaleDateString('en-US', {
		month: 'short',
		day: 'numeric',
		...(year ? { year: 'numeric' } : {})
	});
	if (!d.getHours() && !d.getMinutes()) return date;
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${date}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A value as the table writes it: states capitalised, people by name (Me), numbers rounded. */
export function chipValue(
	value: FilterValue | null,
	kind: ColumnKind | undefined,
	people: People = NOBODY,
	me?: number
): string {
	if (value == null) return '';
	if (kind === 'state' && typeof value === 'string')
		return value.charAt(0).toUpperCase() + value.slice(1);
	if (kind === 'user' && typeof value === 'number')
		return value === me ? 'Me' : (people.get(value)?.name ?? `user ${value}`);
	if (kind === 'time' && typeof value === 'number') return chipDate(value);
	if (kind === 'duration' && typeof value === 'number') return duration(value);
	if (typeof value === 'number') return formatNumber(value);
	return String(value);
}

/** One column's chosen values: `is A`, or `is any of A, B`. */
export function valuesChip(label: string, values: readonly string[], list = false): ChipParts {
	const op = list ? 'includes' : 'is';
	return { label, op: values.length > 1 ? `${op} any of` : op, value: values.join(', ') };
}

/**
 * A top-level filter as a chip's parts. A column's values (an `any` group of `is` on one key)
 * read as one chip; other groups, built in Advanced, as their text alone.
 */
export function chipParts(f: RunFilter, names: ChipNames = {}): ChipParts {
	const kindOf = names.kind ?? (() => undefined);
	const labelOf = names.label ?? ((key: string) => key);
	const people = names.people ?? NOBODY;
	if (isGroup(f)) {
		const [first] = f.filters;
		if (first && !isGroup(first) && isValuesOf(f, first.key)) {
			const kind = kindOf(first.key);
			const values = f.filters.map((g) =>
				chipValue((g as RunCondition).value, kind, people, names.me)
			);
			return valuesChip(labelOf(first.key), values, kind === 'json');
		}
		return { label: '', op: '', value: describeFilters([f], kindOf, false, people) };
	}
	const kind = kindOf(f.key);
	return {
		label: labelOf(f.key),
		op: chipOp(f.op, kind),
		value: UNARY.has(f.op) ? '' : chipValue(f.value, kind, people, names.me)
	};
}

/**
 * How a column filters from its header (the slurm dashboard's ColumnFilter): a list of its
 * values with counts (states, people, lists, true/false and the run's group, job type and GPU
 * type), a range (numbers and durations, or dates), or text it contains.
 */
export type ColumnFilterKind = 'values' | 'range' | 'date' | 'text';

/** Text columns whose values are few and named: listed rather than searched. */
const LISTED: ReadonlySet<string> = new Set(['group', 'job_type', 'gpu_type']);

export function columnFilterKind(key: string, kind: ColumnKind | undefined): ColumnFilterKind {
	if (kind === 'state' || kind === 'user' || kind === 'bool' || kind === 'json' || LISTED.has(key))
		return 'values';
	if (kind === 'num' || kind === 'duration') return 'range';
	if (kind === 'time') return 'date';
	return 'text';
}

/** What a column's filter holds now: the values ticked, a range's ends, or text it contains. */
export interface ColumnFilterState {
	values: string[];
	range: { min?: string; max?: string };
	text: string;
	active: boolean;
}

const isOn = (f: RunFilter, key: string): f is RunCondition => !isGroup(f) && f.key === key;

/** An `any` group of `key is …` conditions: one column's values, as its header sets them. */
const isValuesOf = (f: RunFilter, key: string): f is RunFilterGroup =>
	isGroup(f) &&
	f.match === 'any' &&
	f.filters.length > 0 &&
	f.filters.every((g) => isOn(g, key) && g.op === 'eq');

/** Whether a top-level filter is one a column's header set (and so replaces). */
const ownedByColumn = (f: RunFilter, key: string) =>
	isValuesOf(f, key) ||
	(isOn(f, key) && ['eq', 'gt', 'gte', 'lt', 'lte', 'contains'].includes(f.op));

/** Whether a group is one column's values (`State is any of A, B`): one chip, not a row of them. */
const isOneColumn = (f: RunFilterGroup) => {
	const [first] = f.filters;
	return !!first && !isGroup(first) && isValuesOf(f, first.key);
};

/** Whether a filter shows as one chip: a condition, or one column's values. */
const isChip = (f: RunFilter) => !isGroup(f) || isOneColumn(f);

/**
 * The filters in effect as a row of chips: top-level filters join with and, and an `any` group
 * of chips shows as its chips joined with or (a segment). Anything deeper, which only the
 * advanced editor builds, stays one chip.
 */
export function chipSegments(filters: readonly RunFilter[]): RunFilter[][] {
	const [only] = filters;
	const top =
		filters.length === 1 && isGroup(only) && only.match === 'all' ? only.filters : filters;
	return top.map((f) =>
		isGroup(f) && f.match === 'any' && !isOneColumn(f) && f.filters.every(isChip)
			? [...f.filters]
			: [f]
	);
}

/** The filters for a row of segments (what `chipSegments` reads back). */
export function joinSegments(segments: readonly (readonly RunFilter[])[]): RunFilter[] {
	return segments
		.filter((s) => s.length)
		.map((s): RunFilter => (s.length > 1 ? { match: 'any', filters: [...s] } : s[0]));
}

/** Where the `k`th chip of a row is: its segment and its place in it. */
function chipAt(segments: readonly (readonly RunFilter[])[], k: number): [number, number] {
	let left = k;
	for (let s = 0; s < segments.length; s++) {
		if (left < segments[s].length) return [s, left];
		left -= segments[s].length;
	}
	return [-1, -1];
}

/**
 * The row with the joiner after chip `k` switched: an and joins the chips either side into one
 * or-segment; an or splits its segment there.
 */
export function flipJoiner(segments: readonly (readonly RunFilter[])[], k: number): RunFilter[][] {
	const out = segments.map((s) => [...s]);
	const [s, i] = chipAt(out, k);
	if (s < 0) return out;
	if (i < out[s].length - 1) {
		out.splice(s, 1, out[s].slice(0, i + 1), out[s].slice(i + 1));
	} else if (s + 1 < out.length) {
		out.splice(s, 2, [...out[s], ...out[s + 1]]);
	}
	return out;
}

/** The row without chip `k`; a segment left with one chip is a plain chip again. */
export function dropChip(segments: readonly (readonly RunFilter[])[], k: number): RunFilter[][] {
	const out = segments.map((s) => [...s]);
	const [s, i] = chipAt(out, k);
	if (s >= 0) out[s].splice(i, 1);
	return out.filter((x) => x.length);
}

/** A date-and-time field's text for a time in seconds, in local time. */
export function localDateTime(seconds: number): string {
	const d = new Date(seconds * 1000);
	const pad = (n: number) => String(n).padStart(2, '0');
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The column `key`'s filter among the top-level `filters`, as its header shows it. */
export function readColumnFilter(
	filters: readonly RunFilter[],
	key: string,
	kind: ColumnKind | undefined
): ColumnFilterState {
	const out: ColumnFilterState = { values: [], range: {}, text: '', active: false };
	const show = (v: FilterValue | null) =>
		kind === 'time' && typeof v === 'number' ? localDateTime(v) : String(v ?? '');
	for (const f of filters) {
		if (!ownedByColumn(f, key)) continue;
		out.active = true;
		if (isGroup(f)) out.values.push(...f.filters.map((g) => String((g as RunCondition).value)));
		else if (f.op === 'eq') out.values.push(String(f.value));
		else if (f.op === 'contains') out.text = String(f.value);
		else if (f.op === 'gt' || f.op === 'gte') out.range.min = show(f.value);
		else out.range.max = show(f.value);
	}
	return out;
}

/** A listed value as Flock compares it: people by id, true and false as such. */
function typed(text: string, kind: ColumnKind | undefined): FilterValue {
	if (kind === 'user' && /^\d+$/.test(text)) return Number(text);
	if (kind === 'bool' && (text === 'true' || text === 'false')) return text === 'true';
	return text;
}

/**
 * The filters with column `key`'s header filter set to `to` (or cleared, without it): what the
 * header set before is replaced, and every other filter stays as it was. Several values hold
 * as any of them; a range's ends hold together.
 */
export function setColumnFilter(
	filters: readonly RunFilter[],
	key: string,
	kind: ColumnKind | undefined,
	to?: { values?: readonly string[]; range?: { min?: string; max?: string }; text?: string }
): RunFilter[] {
	const rest = filters.filter((f) => !ownedByColumn(f, key));
	const added: RunFilter[] = [];
	const values = to?.values ?? [];
	if (values.length === 1) added.push({ key, op: 'eq', value: typed(values[0], kind) });
	else if (values.length > 1)
		added.push({
			match: 'any',
			filters: values.map((v) => ({ key, op: 'eq' as const, value: typed(v, kind) }))
		});
	for (const [end, op] of [
		['min', 'gte'],
		['max', 'lte']
	] as const) {
		const text = to?.range?.[end]?.trim();
		if (!text) continue;
		const parsed = parseValue(text, kind);
		if ('value' in parsed) added.push({ key, op, value: parsed.value });
	}
	const text = to?.text?.trim();
	if (text) added.push({ key, op: 'contains', value: text });
	return [...rest, ...added];
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
