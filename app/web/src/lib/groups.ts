/**
 * Run groups (025): their URLs, the calls the pages make, and the rules the pages apply to a
 * group's runs: column kinds (D185), configurations and the best of them (D184), default names
 * (D192) and the create and edit page's row looks (D190). The API computes the same kinds and
 * bests for its own answers (`core/group_analysis.py`); the pages repeat them only where they
 * work on runs the API hasn't seen yet (a draft) or lay a group's rows out (configurations).
 */

import { error } from '@sveltejs/kit';
import type { ResolvedPathname } from '$app/types';
import type { ColumnKind } from './types.ts';
import {
	flockFilter,
	isGroup,
	writeFilters,
	type FilterOp,
	type FilterValue,
	type RunCondition,
	type RunFilter
} from './workspace/filters.ts';
import { projectHref } from './workspace/url.ts';

// ----------------------------------------------------------------------------------------------
// What the API answers (`app/api/src/api/groups.py`)

/** D185: a config key's kind within a group's runs. */
export type Kind = 'varies' | 'repeat' | 'same' | 'unique';

/** Lowest (`asc`) or highest (`desc`) is best (D184). */
export type BestOrder = 'asc' | 'desc';

/** A Mongo-style filter, as `POST .../runs/query` takes it (D77). */
export type FlockFilter = Record<string, unknown>;

/** A group's best run, or best configuration's best run when it has repeats (D184). */
export interface GroupBest {
	run: string;
	name: string;
	/** The mean over the configuration's runs when the group has repeats. */
	value: number | null;
	/** The best run's own value; differs from `value` when the group has repeats. */
	run_value?: number | null;
	std: number | null;
	n: number;
}

/** A saved group, as `GET .../groups` lists it. */
export interface Group {
	id: number;
	name: string;
	color: string;
	filter: FlockFilter;
	run_count: number;
	/** Runs matching its filter that are neither in it nor dropped (D183). */
	new_count: number;
	/** Null: the project's default. */
	best_key: string | null;
	best_order: BestOrder | null;
	best: GroupBest | null;
	created_at: string;
	loaded_at: string;
}

export interface GroupList {
	groups: Group[];
	/** Runs in no saved group (D187). */
	ungrouped_count: number;
	/**
	 * The project's best-by setting in effect (D184); null until someone picks one. A group's
	 * own `best_key` is its override, null for this one.
	 */
	best_key: string | null;
	best_order: BestOrder | null;
	/** Whether the viewer may use the assistant (D191): a key, the org's setting, a member. */
	assistant: boolean;
}

/** How a group's charts draw seeds (D189). */
export type SeedsMode = 'std' | 'minmax' | 'runs';

/** A group's chart settings (D189), saved on the group. */
export interface GroupView {
	split: string | null;
	color: string | null;
	seeds: SeedsMode;
	/** Whether the charts draw every run or only each group's best. */
	show: ShowMode;
}

export type ShowMode = 'all' | 'best';

/** One group with its runs, as `GET .../groups/{group}` and `PATCH` answer. */
export interface GroupDetail extends Group {
	runs: string[];
	dropped: string[];
	/** The repeat key set by hand, else the one detected (D185). */
	repeat_key: string | null;
	/** How each member joined (D193): matched by the filter, or added by hand. */
	added_by?: Record<string, 'filter' | 'hand'>;
	/** Kinds set by hand (D185). */
	columns: Record<string, Kind>;
	view: Partial<GroupView> & Record<string, unknown>;
}

/** A config key of a group's runs (`GET .../groups/{group}/columns`). */
export interface GroupColumn {
	key: string;
	kind: Kind;
	distinct: number;
	overridden: boolean;
}

/** A row of Compare (D188): a group's best, or an ungrouped run. */
export interface CompareRow {
	/** Null for an ungrouped run. */
	group: number | null;
	name: string;
	color: string | null;
	/** The row's best-by setting: a group ranked by another metric comes last, greyed (D188). */
	best_key?: string | null;
	best_order?: BestOrder | null;
	/** Null for a group with no best (no run has a finite value). */
	run: string | null;
	run_name: string | null;
	/** The best configuration's value: the mean over its repeats when it has some. */
	value: number | null;
	/** The best run's own value. */
	run_value?: number | null;
	std: number | null;
	n: number;
	runs: number;
	/** Every member's value of the best-by column (a single run's own); absent from older APIs. */
	values?: number[];
	config: Record<string, unknown>;
	/** The run's finite `summary.*` numbers, for Compare's suggested metric columns. */
	summary?: Record<string, number>;
}

export interface Comparison {
	best_key: string | null;
	best_order: BestOrder | null;
	/** Config keys that differ between the rows' best runs. */
	keys: string[];
	rows: CompareRow[];
	/** Best first, all of them. */
	ungrouped: CompareRow[];
	ungrouped_count: number;
}

/** A suggested group (D186). */
export interface Suggestion {
	/** Hex sha256 of its sorted run ids, which dismissing it remembers. */
	signature: string;
	source: 'wandb_group' | 'config_diff';
	name: string;
	/** `{"group": name}` for a W&B group; null for a cluster, whose runs are hand picks. */
	filter: FlockFilter | null;
	runs: string[];
}

/** One reading of a request: a condition and how likely the model found it (D191). */
export interface Reading extends RunCondition {
	probability: number;
}

/** A condition the assistant set, with the readings it didn't pick, best first. */
export interface ActionCondition extends Reading {
	alternatives: Reading[];
}

/** One assistant turn's answer (`POST .../groups/assist`, D191). */
export interface AssistAction {
	type: 'filter' | 'add_runs' | 'drop_runs' | 'set_best' | 'none';
	conditions: ActionCondition[];
	runs: { id: string; name: string; probability: number }[];
	best: { key: string; order: BestOrder } | null;
}

/** One cell of a proposal's coverage grid: the runs at one value (pair), and their best. */
export interface ProposalCell {
	x: unknown;
	y: unknown | null;
	runs: number;
	/** The cell's best metric value; null when no run has one. */
	best: number | null;
	/** The run with that value. */
	run: string | null;
}

/** How a proposal's runs cover its one or two axes. */
export interface ProposalGrid {
	x: string;
	y: string | null;
	xs: unknown[];
	ys: unknown[] | null;
	cells: ProposalCell[];
}

/** A group the assistant proposes on the create page (`POST .../groups/propose`). */
export interface Proposal {
	id: string;
	name: string;
	/** Why these runs belong together: the one change they compare. */
	why: string;
	runs: string[];
	filter: FlockFilter | null;
	/** The config keys that vary inside it, and the values each takes. */
	axes: string[];
	values: Record<string, unknown[]>;
	grid: ProposalGrid | null;
	best_key: string | null;
	best_order: BestOrder | null;
	/**
	 * The best run, with its own `value`. With repeats, `mean` ± `std` over `n` runs is its
	 * configuration's, which ranks it.
	 */
	best: {
		run: string;
		name: string;
		value: number;
		mean?: number | null;
		std?: number | null;
		n?: number;
	} | null;
	repeat_key: string | null;
	/** The body `POST .../groups` takes to create it, its name unique among saved groups. */
	create?: {
		name: string;
		runs: string[];
		filter: FlockFilter | null;
		best_key: string | null;
		best_order: BestOrder | null;
	};
}

/** One message of the proposal conversation, as the propose route takes it. */
export interface ProposeMessage {
	role: 'user' | 'assistant';
	content: string;
}

/** `POST .../groups/propose`'s answer. */
export interface ProposeAnswer {
	message: string;
	proposals: Proposal[];
	/** Live runs in none of the proposals and no saved group. */
	ungrouped: number;
	/** Runs the assistant read. */
	runs: number;
}

/** `POST .../runs/query`'s answer: column-oriented cells aligned with `ids`. */
export interface RunsResult {
	total: number;
	offset: number;
	limit: number;
	ids: string[];
	columns: string[];
	data: Record<string, unknown[]>;
	all_ids?: string[];
}

// ----------------------------------------------------------------------------------------------
// URLs

/** Compare: every group's best, side by side (D188). */
export const groupsHref = (entity: string, project: string) =>
	`${projectHref(entity, project)}/groups` as ResolvedPathname;

/** Compare's ungrouped section (D187), where the sidebar's **Ungrouped runs** leads. */
export const ungroupedHref = (entity: string, project: string) =>
	`${groupsHref(entity, project)}#ungrouped` as ResolvedPathname;

/** A group's page (D189). */
export const groupHref = (entity: string, project: string, group: number) =>
	`${groupsHref(entity, project)}/${group}` as ResolvedPathname;

/** What a new group starts with: a suggestion's runs and filter, or an ungrouped run. */
export interface Prefill {
	runs?: readonly string[];
	filters?: readonly RunFilter[];
	name?: string;
}

/** The create page (D190), optionally prefilled. */
export function newGroupHref(entity: string, project: string, prefill: Prefill = {}) {
	const search = new URLSearchParams();
	if (prefill.runs?.length) search.set('runs', prefill.runs.join(','));
	const filters = writeFilters(prefill.filters ?? []);
	if (filters) search.set('filters', filters);
	if (prefill.name) search.set('name', prefill.name);
	const qs = search.toString();
	return `${groupsHref(entity, project)}/new${qs ? `?${qs}` : ''}` as ResolvedPathname;
}

/** A group's edit page (D190); `fresh` opens it on its new matches (D183). */
export const editGroupHref = (entity: string, project: string, group: number, fresh = false) =>
	`${groupHref(entity, project, group)}/edit${fresh ? '?new=1' : ''}` as ResolvedPathname;

/** What a create page's URL asks for (`newGroupHref`). */
export function readPrefill(params: URLSearchParams, read: (s: string | null) => RunFilter[]) {
	return {
		runs: (params.get('runs') ?? '').split(',').filter(Boolean),
		filters: read(params.get('filters')),
		name: params.get('name')?.trim() || null
	};
}

// ----------------------------------------------------------------------------------------------
// The dashboard's endpoints (`src/routes/api/projects/[entity]/[project]/groups`), which call
// Flock as the signed-in user, and the calls that go through them.

/** What the group pages' loads depend on: a change to any group invalidates it. */
export const GROUPS = 'flock:groups';

const projectApi = (entity: string, project: string) =>
	`/api/projects/${encodeURIComponent(entity)}/${encodeURIComponent(project)}`;

/** The endpoint of one of a project's group routes (`op` after `groups/`), with a query. */
export function groupsApi(
	entity: string,
	project: string,
	op = '',
	query: Record<string, string | null | undefined> = {}
): string {
	const search = new URLSearchParams();
	for (const [name, value] of Object.entries(query)) if (value != null) search.set(name, value);
	const qs = search.toString();
	return `${projectApi(entity, project)}/groups${op ? `/${op}` : ''}${qs ? `?${qs}` : ''}`;
}

/** The project's runs query (`POST .../runs/query`), for runs not yet in a group. */
export const runsQueryApi = (entity: string, project: string) =>
	`${projectApi(entity, project)}/runs/query`;

/** A refused call: the message Flock gave, with its status. */
export class GroupsError extends Error {
	status: number;

	constructor(status: number, message: string) {
		super(message);
		this.status = status;
	}
}

type Fetch = typeof fetch;

interface CallInit {
	method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
	body?: unknown;
	signal?: AbortSignal;
}

/** A call to one of the dashboard's endpoints; refusals throw a `GroupsError`. */
export async function groupsCall<T>(url: string, init: CallInit = {}, fetchFn: Fetch = fetch) {
	const res = await fetchFn(url, {
		method: init.method ?? 'GET',
		signal: init.signal,
		...(init.body === undefined
			? {}
			: { headers: { 'content-type': 'application/json' }, body: JSON.stringify(init.body) })
	});
	const json = res.status === 204 ? null : await res.json().catch(() => null);
	if (!res.ok) {
		const body = (json ?? {}) as { message?: string; error?: string };
		const message =
			res.status === 409
				? 'A group with this name exists'
				: (body.message ?? body.error ?? `Flock answered ${res.status}`);
		throw new GroupsError(res.status, message);
	}
	return json as T;
}

/** The same from a page's load: a refusal is the error page, with Flock's message. */
export async function loadCall<T>(fetchFn: Fetch, url: string, init: CallInit = {}): Promise<T> {
	try {
		return await groupsCall<T>(url, init, fetchFn);
	} catch (err) {
		if (err instanceof GroupsError) error(err.status as 400, err.message);
		throw err;
	}
}

/** A project's runs with the given columns: the query reads at most this many. */
export const MAX_RUNS = 2000;

/**
 * Runs as rows from a runs query's columns: `display_name` and the run attributes the pages
 * read, and everything else (`config.*`, `summary.*`) as cells.
 */
export interface RunRow {
	id: string;
	name: string;
	state: string;
	created_at: number | null;
	group: string | null;
	cells: Record<string, unknown>;
}

export function rowsOf(result: RunsResult): RunRow[] {
	const col = (key: string) => result.data[key] ?? [];
	return result.ids.map((id, i) => {
		const cells: Record<string, unknown> = {};
		for (const key of result.columns) {
			const v = result.data[key]?.[i];
			if (v !== null && v !== undefined) cells[key] = v;
		}
		const name = col('display_name')[i];
		const created = col('created_at')[i];
		const group = col('group')[i];
		return {
			id,
			name: typeof name === 'string' && name ? name : id,
			state: String(col('state')[i] ?? 'finished'),
			created_at: typeof created === 'number' ? created : null,
			group: typeof group === 'string' ? group : null,
			cells
		};
	});
}

/** A row's config, as the kind and name rules read it: `config.*` cells only. */
export function configOf(row: RunRow): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	for (const [key, v] of Object.entries(row.cells)) if (key.startsWith('config.')) out[key] = v;
	return out;
}

/** The finite number in a cell, or null (NaN, infinities and the rest are never best, D184). */
export function finite(v: unknown): number | null {
	return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

// ----------------------------------------------------------------------------------------------
// Filters: the group's Mongo-style filter (D183) as the workspace's filters, and back

/** The Mongo-style filter for some of the workspace's filters; `{}` for every run. */
export const toFlockFilter = (filters: readonly RunFilter[]): FlockFilter =>
	(flockFilter('', filters) as FlockFilter | null) ?? {};

const OPS: Record<string, FilterOp> = {
	$eq: 'eq',
	$ne: 'ne',
	$gt: 'gt',
	$gte: 'gte',
	$lt: 'lt',
	$lte: 'lte'
};

const isPlain = (v: unknown): v is FilterValue =>
	typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean';

const isObject = (v: unknown): v is Record<string, unknown> =>
	!!v && typeof v === 'object' && !Array.isArray(v);

/** The text a `contains` condition matches, from the `$regex` `flockFilter` writes. */
function containedText(regex: unknown): string | null {
	if (typeof regex !== 'string' || !regex.startsWith('(?i)')) return null;
	const body = regex.slice(4);
	// Only a literal: every special character escaped.
	if (/(^|[^\\])(\\\\)*[.*+?^${}()|[\]]/.test(body)) return null;
	return body.replace(/\\(.)/g, '$1');
}

/** The conditions on one column, or null for a comparison the popover can't show. */
function columnConditions(key: string, cond: unknown): RunCondition[] | null {
	if (isPlain(cond)) return [{ key, op: 'eq', value: cond }];
	if (!isObject(cond)) return null;
	const out: RunCondition[] = [];
	for (const [name, v] of Object.entries(cond)) {
		if (name in OPS && isPlain(v)) out.push({ key, op: OPS[name], value: v });
		else if (name === '$exists' && typeof v === 'boolean')
			out.push({ key, op: v ? 'exists' : 'missing', value: null });
		else if (name === '$regex' && containedText(v) !== null)
			out.push({ key, op: 'contains', value: containedText(v) });
		else if (name === '$in' && Array.isArray(v) && v.length === 1 && isPlain(v[0]))
			out.push({ key, op: 'eq', value: v[0] });
		else return null;
	}
	return out.length ? out : null;
}

/** The filters every one of `filter`'s parts holds for (an implicit or explicit `$and`). */
function allOf(filter: Record<string, unknown>): RunFilter[] | null {
	const out: RunFilter[] = [];
	for (const [key, value] of Object.entries(filter)) {
		if (key === '$and') {
			if (!Array.isArray(value)) return null;
			for (const part of value) {
				const inner = isObject(part) ? allOf(part) : null;
				if (!inner) return null;
				out.push(...inner);
			}
		} else if (key === '$or') {
			if (!Array.isArray(value)) return null;
			const parts: RunFilter[] = [];
			for (const part of value) {
				const inner = isObject(part) ? allOf(part) : null;
				if (!inner) return null;
				if (inner.length === 1) parts.push(inner[0]);
				else if (inner.length) parts.push({ match: 'all', filters: inner });
			}
			if (parts.length === 1) out.push(parts[0]);
			else if (parts.length) out.push({ match: 'any', filters: parts });
		} else if (key.startsWith('$')) {
			return null;
		} else {
			const conditions = columnConditions(key, value);
			if (!conditions) return null;
			out.push(...conditions);
		}
	}
	return out;
}

/**
 * A group's filter as the workspace's filters (what the Filter popover edits and chips show):
 * the inverse of `flockFilter`. Null when the filter uses something the popover can't show
 * (`$in` of several values, a regular expression); the pages then show it as JSON.
 */
export function fromFlockFilter(filter: unknown): RunFilter[] | null {
	if (filter == null) return [];
	if (!isObject(filter)) return null;
	return allOf(filter);
}

/** The top-level conditions of some filters (the assistant reads only those, D191). */
export const topConditions = (filters: readonly RunFilter[]): RunCondition[] =>
	filters.filter((f): f is RunCondition => !isGroup(f));

/**
 * Filters with `conditions` set: each replaces the top-level conditions on its column, and the
 * rest are kept (an assistant's **Set filters**, D191).
 */
export function setConditions(
	filters: readonly RunFilter[],
	conditions: readonly RunCondition[]
): RunFilter[] {
	const keys = new Set(conditions.map((c) => c.key));
	const kept = filters.filter((f) => isGroup(f) || !keys.has(f.key));
	return [...kept, ...conditions.map(({ key, op, value }) => ({ key, op, value }))];
}

// ----------------------------------------------------------------------------------------------
// Column kinds (D185), mirroring `classify_columns`

/** A comparable stand-in for a value: equal values (every NaN, equal lists) read the same. */
export function valueKey(v: unknown): string {
	if (v === null || v === undefined) return 'none';
	if (typeof v === 'boolean') return `b:${v}`;
	if (typeof v === 'number') return Number.isNaN(v) ? 'nan' : `n:${v}`;
	if (typeof v === 'string') return `s:${v}`;
	if (Array.isArray(v)) return `l:[${v.map(valueKey).join(',')}]`;
	if (typeof v === 'object')
		return `d:{${Object.keys(v)
			.sort()
			.map((k) => `${k}=${valueKey((v as Record<string, unknown>)[k])}`)
			.join(',')}}`;
	return `o:${String(v)}`;
}

/** D185: a group with fewer runs than this treats strings distinct in every run as Varies. */
export const UNIQUE_MIN_RUNS = 4;

/** A key named like a seed: `seed`, `*_seed`, `random_state` or `rng` (its last segment). */
export function isRepeatName(key: string): boolean {
	const name = displayKey(key).split('.').pop()!.toLowerCase();
	return name === 'seed' || name === 'random_state' || name === 'rng' || name.endsWith('_seed');
}

/** A key as names and headers show it, without `config.`. */
export const displayKey = (key: string) => key.replace(/^config\./, '');

function kindOf(values: unknown[], runs: number): Kind {
	const counts = new Map<string, number>();
	for (const v of values) counts.set(valueKey(v), (counts.get(valueKey(v)) ?? 0) + 1);
	if (counts.size <= 1) return values.length === runs || !values.length ? 'same' : 'varies';
	if (Math.max(...counts.values()) >= 2) return 'varies';
	const texts = values.every((v) => typeof v === 'string' || (typeof v === 'object' && v !== null));
	return texts && runs >= UNIQUE_MIN_RUNS ? 'unique' : 'varies';
}

/**
 * Each config key of `configs` (with a value), sorted, and its kind (D185). A repeat name wins
 * over the value rules, and `overrides` over both.
 */
export function classifyColumns(
	configs: readonly Record<string, unknown>[],
	overrides: Readonly<Record<string, Kind>> = {}
): Record<string, Kind> {
	const seen = new Map<string, unknown[]>();
	for (const config of configs)
		for (const [key, v] of Object.entries(config)) {
			if (v === null || v === undefined) continue;
			if (!seen.has(key)) seen.set(key, []);
			seen.get(key)!.push(v);
		}
	const out: Record<string, Kind> = {};
	for (const key of [...seen.keys()].sort()) {
		out[key] =
			overrides[key] ?? (isRepeatName(key) ? 'repeat' : kindOf(seen.get(key)!, configs.length));
	}
	return out;
}

/** The keys of one kind, sorted. */
export const keysOf = (kinds: Readonly<Record<string, Kind>>, kind: Kind) =>
	Object.keys(kinds)
		.filter((k) => kinds[k] === kind)
		.sort();

// ----------------------------------------------------------------------------------------------
// Configurations and the best (D184), mirroring `best`

export interface Configuration<R> {
	/** The Varies values that define it, by key; empty without a repeat key (a run each). */
	values: [string, unknown][];
	rows: R[];
	/** The mean over its runs with a finite value; null without any. */
	value: number | null;
	/** Population std, with 2 or more finite values. */
	std: number | null;
	n: number;
}

/**
 * Runs as configurations, best first (D184). Without a repeat key each run is its own. With
 * one, runs sharing every Varies value form one, whose value is the mean over its finite values.
 * Configurations without a value come last; ties keep the runs' id order.
 */
export function configurations<R extends { id: string; cells: Record<string, unknown> }>(
	rows: readonly R[],
	kinds: Readonly<Record<string, Kind>>,
	key: string | null,
	order: BestOrder
): Configuration<R>[] {
	const varying = keysOf(kinds, 'varies');
	const repeats = keysOf(kinds, 'repeat').length > 0;
	const sorted = [...rows].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
	const buckets = new Map<string, R[]>();
	for (const row of sorted) {
		const id = repeats ? varying.map((k) => valueKey(row.cells[k])).join('|') : `run:${row.id}`;
		if (!buckets.has(id)) buckets.set(id, []);
		buckets.get(id)!.push(row);
	}
	const sign = order === 'asc' ? 1 : -1;
	const out = [...buckets.values()].map((members): Configuration<R> => {
		const found = key
			? members.map((r) => finite(r.cells[key])).filter((v): v is number => v !== null)
			: [];
		const mean = found.length ? found.reduce((a, b) => a + b, 0) / found.length : null;
		const std =
			mean !== null && found.length >= 2
				? Math.sqrt(found.reduce((a, v) => a + (v - mean) ** 2, 0) / found.length)
				: null;
		return {
			values: repeats ? varying.map((k) => [k, members[0].cells[k]]) : [],
			rows: members,
			value: mean,
			std,
			n: found.length
		};
	});
	// Stable: configurations of equal value keep their runs' id order.
	return out.sort((a, b) => {
		if (a.value === null || b.value === null)
			return (a.value === null ? 1 : 0) - (b.value === null ? 1 : 0);
		return sign * (a.value - b.value);
	});
}

/** The best of `rows` by a column: the best configuration's best run (D184); null without one. */
export function bestRun<R extends { id: string; cells: Record<string, unknown> }>(
	configs: readonly Configuration<R>[],
	key: string | null,
	order: BestOrder
): R | null {
	const top = configs[0];
	if (!key || !top || top.value === null) return null;
	const sign = order === 'asc' ? 1 : -1;
	let best: R | null = null;
	for (const r of top.rows) {
		const v = finite(r.cells[key]);
		if (v === null) continue;
		if (!best || sign * (v - finite(best.cells[key])!) < 0) best = r;
	}
	return best;
}

// ----------------------------------------------------------------------------------------------
// Names (D192), mirroring `group_name`

/** One key is named by its values when it has at most this many. */
const MAX_NAMED_VALUES = 3;

function formatName(v: unknown): string {
	if (typeof v === 'boolean') return v ? 'true' : 'false';
	if (Array.isArray(v)) return `[${v.map(formatName).join(', ')}]`;
	return String(v);
}

function compareValues(a: unknown, b: unknown): number {
	if (typeof a === 'number' && typeof b === 'number') return a - b;
	const x = valueKey(a);
	const y = valueKey(b);
	return x < y ? -1 : x > y ? 1 : 0;
}

/** The name from the Varies keys, or null when nothing varies. */
function template(configs: readonly Record<string, unknown>[], kinds: Record<string, Kind>) {
	const keys = keysOf(kinds, 'varies');
	const names = keys.map(displayKey);
	if (!keys.length) return null;
	if (keys.length === 1) {
		const found = new Map<string, unknown>();
		for (const c of configs) {
			const v = c[keys[0]];
			if (v !== null && v !== undefined) found.set(valueKey(v), v);
		}
		const values = [...found.values()].sort(compareValues);
		if (values.length && values.every((v) => typeof v === 'boolean')) return `${names[0]} on / off`;
		if (!values.length) return names[0];
		if (values.length <= MAX_NAMED_VALUES)
			return `${names[0]}: ${values.map(formatName).join(', ')}`;
		return `${names[0]}: ${values.length} values`;
	}
	if (keys.length === 2) return names.join(' × ');
	return `${names[0]} × ${names[1]} + ${keys.length - 2} more`;
}

/** The single state the filters fix at their top level (`state is finished`), or null. */
export function fixedState(filters: readonly RunFilter[]): string | null {
	for (const f of filters)
		if (!isGroup(f) && f.key === 'state' && f.op === 'eq' && typeof f.value === 'string')
			return f.value;
	return null;
}

/**
 * A new group's name (D192): `lr × batch_size` for two Varies keys, `qk_norm on / off` for one
 * boolean, `data_mix: web-v2, web-v3` for one key with up to 3 values; `N runs` when nothing
 * varies. A W&B group's name comes first, and `, finished` follows when the filters fix the state.
 */
export function groupName(
	configs: readonly Record<string, unknown>[],
	filters: readonly RunFilter[],
	wandbGroup: string | null = null
): string {
	const named = template(configs, classifyColumns(configs));
	let name = wandbGroup
		? named
			? `${wandbGroup}, ${named}`
			: wandbGroup
		: (named ?? `${configs.length} run${configs.length === 1 ? '' : 's'}`);
	const state = fixedState(filters);
	if (state) name += `, ${state}`;
	return name;
}

/** The W&B group the filters pick (`group is pretrain-1b`), as a suggestion's filter names it. */
export function wandbGroupOf(filters: readonly RunFilter[]): string | null {
	for (const f of filters)
		if (!isGroup(f) && f.key === 'group' && f.op === 'eq' && typeof f.value === 'string')
			return f.value;
	return null;
}

// ----------------------------------------------------------------------------------------------
// The create and edit page's rows (D190)

/**
 * How a run sits in a draft group:
 * - `filter`: matched by the filters, in (blue);
 * - `hand`: ticked by hand, not matched (green);
 * - `out`: matched, or a member, but unticked by hand (struck);
 * - `member`: already in the group being edited;
 * - `new`: a new match of the group being edited, not ticked yet (amber);
 * - `dropped`: dropped from it before (struck);
 * - `none`: not in it.
 */
export type Look = 'filter' | 'hand' | 'out' | 'member' | 'new' | 'dropped' | 'none';

/** What a draft knows: the runs the filters match, the group's own lists, and the hand ticks. */
export interface Draft {
	matched: ReadonlySet<string>;
	/** The group's runs when editing one; empty when creating. */
	members: ReadonlySet<string>;
	dropped: ReadonlySet<string>;
	/** Ticked by hand: in, whatever the filters say. */
	added: ReadonlySet<string>;
	/** Unticked by hand: out, whatever the filters say. */
	removed: ReadonlySet<string>;
	/** Members that were added by hand (the group's `added_by`), labeled so. */
	byHand?: ReadonlySet<string>;
	/** Editing a saved group: its runs are the base, and matches are only offered. */
	editing: boolean;
}

/** Whether a run is in the draft group. */
export function inDraft(d: Draft, id: string): boolean {
	if (d.added.has(id)) return true;
	if (d.removed.has(id)) return false;
	return d.editing ? d.members.has(id) : d.matched.has(id);
}

/**
 * Whether a run is a new match (D183): matched by the filters, neither in the group nor
 * dropped from it. Only a saved group has them.
 */
export const isNewMatch = (d: Draft, id: string) =>
	d.editing && d.matched.has(id) && !d.members.has(id) && !d.dropped.has(id);

export function lookOf(d: Draft, id: string): Look {
	if (d.editing) {
		if (d.members.has(id)) return d.removed.has(id) ? 'out' : d.byHand?.has(id) ? 'hand' : 'member';
		if (d.added.has(id)) return 'hand';
		if (d.dropped.has(id)) return 'dropped';
		return isNewMatch(d, id) ? 'new' : 'none';
	}
	if (d.added.has(id)) return d.matched.has(id) ? 'filter' : 'hand';
	if (d.matched.has(id)) return d.removed.has(id) ? 'out' : 'filter';
	return 'none';
}

/** A run ticked or unticked: the hand ticks after it. */
export function tick(
	d: Draft,
	id: string,
	on: boolean
): { added: Set<string>; removed: Set<string> } {
	const added = new Set(d.added);
	const removed = new Set(d.removed);
	added.delete(id);
	removed.delete(id);
	const base = d.editing ? d.members.has(id) : d.matched.has(id);
	if (on && !base) added.add(id);
	if (!on && base) removed.add(id);
	return { added, removed };
}

/**
 * What saving an edited group sends (D183, D190): runs to add (dropped runs ticked back in
 * too: `add` takes them back, where `restore` would only offer them again) and members to drop.
 * The filter only changes which runs are offered; it never removes a member.
 */
export function changes(d: Draft) {
	const add = [...d.added].filter((id) => !d.members.has(id));
	const drop = [...d.removed].filter((id) => d.members.has(id));
	return { add: add.sort(), drop: drop.sort() };
}

/** Counts for the footer: runs in, and how each came to be in or out. */
export function draftCounts(d: Draft, ids: readonly string[]) {
	const out = { in: 0, filter: 0, hand: 0, out: 0, member: 0, new: 0, dropped: 0, none: 0 };
	for (const id of ids) {
		if (inDraft(d, id)) out.in += 1;
		out[lookOf(d, id)] += 1;
	}
	return out;
}

// ----------------------------------------------------------------------------------------------
// Display

/** The kind of a column for the Filter popover, from its key and the values seen (D185 aside). */
export function columnKind(key: string, values: readonly unknown[]): ColumnKind | undefined {
	if (key === 'state') return 'state';
	if (key === 'created_at' || key === 'updated_at' || key === 'heartbeat_at') return 'time';
	if (key === 'runtime') return 'duration';
	if (key === 'owner') return 'user';
	if (key === 'tags') return 'json';
	const found = values.filter((v) => v !== null && v !== undefined);
	if (!found.length) return undefined;
	if (found.every((v) => typeof v === 'number')) return 'num';
	if (found.every((v) => typeof v === 'boolean')) return 'bool';
	if (found.every((v) => typeof v === 'string')) return 'str';
	return 'json';
}

/** A summary column as menus name it: `val/loss`. */
export const metricLabel = (key: string) => key.replace(/^summary\./, '');

/** `val/loss ↓`: the metric and its direction, lowest (↓) or highest (↑) is best. */
export const bestLabel = (key: string | null, order: BestOrder | null) =>
	key ? `${metricLabel(key)} ${order === 'desc' ? '↑' : '↓'}` : 'Not set';

/** `val/loss, lowest`: the same in words, for tooltips and screen readers. */
export const bestWords = (key: string | null, order: BestOrder | null) =>
	key ? `${metricLabel(key)}, ${order === 'desc' ? 'highest' : 'lowest'}` : 'Not set';

/** A value's change from another's, signed with a real minus: `−0.055`. */
export function formatDelta(v: number): string {
	const digits = Math.abs(v) >= 1 ? 2 : 3;
	const s = Math.abs(v).toFixed(digits);
	return v < 0 ? `−${s}` : v > 0 ? `+${s}` : s;
}

/** Whether `value` beats `other` in `order`. */
export const beats = (value: number, other: number, order: BestOrder) =>
	order === 'asc' ? value < other : value > other;

/**
 * A group's chart settings (D189: Group by, Color by and how repeats are drawn) with defaults
 * for what isn't saved. One varying key: Color by it. More: Group by the one with the fewest
 * values (2 to 8 of them, `distinct` counts them; else the second key), and Color by the one
 * with the most of the rest (else the first), so a few hues are each shaded by a sweep's axis.
 * Color by may also be a repeat key (`repeats`).
 */
export function viewOf(
	saved: Partial<GroupView> | null | undefined,
	varies: readonly string[],
	distinct: Readonly<Record<string, number>> = {},
	repeats: readonly string[] = []
): GroupView {
	const known = (k: unknown, also: readonly string[] = []) =>
		typeof k === 'string' && (varies.includes(k) || also.includes(k)) ? k : null;
	const seeds: SeedsMode =
		saved?.seeds === 'minmax' || saved?.seeds === 'runs' ? saved.seeds : 'std';
	const show: ShowMode = saved?.show === 'best' ? 'best' : 'all';
	if (saved && ('split' in saved || 'color' in saved))
		return { split: known(saved.split), color: known(saved.color, repeats), seeds, show };
	if (varies.length === 1) return { split: null, color: varies[0], seeds, show };
	if (varies.length < 2) return { split: null, color: null, seeds, show };
	const count = (k: string) => distinct[k] ?? 0;
	const few = varies
		.filter((k) => count(k) >= 2 && count(k) <= 8)
		.sort((a, b) => count(a) - count(b))[0];
	if (!few) return { split: varies[1], color: varies[0], seeds, show };
	const rest = varies.filter((k) => k !== few);
	const most = [...rest].sort((a, b) => count(b) - count(a))[0];
	return { split: few, color: count(most) ? most : rest[0], seeds, show };
}

/**
 * Labels for names read side by side (a chart's legend) that keep what tells them apart:
 * with a long shared start (`batch_size × dropout + 17 more (sweep-3evqlkwy)` and
 * `… (sweep-s2g2q1q2)`), the start is cut to `…` at a word boundary, so a truncated label
 * still shows its difference. Other names are kept whole.
 */
export function distinctLabels(names: readonly string[], min = 12): string[] {
	const unique = [...new Set(names)];
	if (unique.length < 2) return [...names];
	let n = 0;
	const first = unique[0];
	while (n < first.length && unique.every((s) => s[n] === first[n])) n++;
	// Back to the start of the word the names part in, so `sweep-…` stays whole.
	while (n > 0 && !/[\s(,×·]/.test(first[n - 1])) n--;
	if (first[n - 1] === '(') n--; // keep a parenthesis paired
	if (n < min) return [...names];
	return names.map((s) => `…${s.slice(n)}`);
}

/**
 * Whether a summary column is one Flock or wandb keeps for itself (`_step`, `_runtime`,
 * `_wandb.*`, …) rather than a metric a run logged; those are never offered to rank by.
 */
export function isInternalKey(key: string): boolean {
	const bare = key.startsWith('summary.') ? key.slice('summary.'.length) : key;
	return bare.split(/[./]/).some((part) => part.startsWith('_'));
}

/** The history keys a group's charts plot: the best-by metric first, then its other metrics. */
export function chartMetrics(summaryKeys: readonly string[], bestKey: string | null, max = 12) {
	const keys = summaryKeys
		.map(metricLabel)
		.filter((k) => !k.startsWith('_') && !k.startsWith('system/'));
	const best = bestKey ? metricLabel(bestKey) : null;
	const out = best ? [best, ...keys.filter((k) => k !== best)] : keys;
	return out.slice(0, max);
}
